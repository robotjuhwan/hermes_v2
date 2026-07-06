from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.daily_discovery import enrich_discovery_result


WIKI_SECTION_ORDER = [
    "Current Stance",
    "Durable Facts",
    "Evidence Links",
    "Trading History",
    "Lessons",
    "Contradictions",
    "Open Questions",
    "Next Context Pack Summary",
]

PAGE_TYPE_EXTRA_SECTIONS: dict[str, list[str]] = {
    "playbook": [
        "Entry Conditions",
        "Exit Conditions",
        "Failure Modes",
        "Performance Evidence",
    ],
    "regime": [
        "Current Markers",
        "Risk Posture",
        "Useful Playbooks",
        "Invalidated Playbooks",
    ],
    "lesson": [
        "Observed Pattern",
        "Evidence",
        "Behavioral Change",
    ],
    "core": [
        "Principle",
        "Scope",
        "Where It Applies",
        "Where It Does Not Apply",
    ],
    "risk": [
        "Risk State",
        "Lane Authority",
        "Failed Disciplines",
        "Repair Queue",
        "Aggression Contract",
    ],
    "ops": [
        "Action Pressure",
        "Opportunity Pipeline",
        "Missed Upside",
        "Creative Hypotheses",
        "Resolution Queue",
        "Probe Mandate",
    ],
    "research": [
        "Coverage Matrix",
        "Action Batches",
        "Freshness",
        "Data Gaps",
        "Actionability",
    ],
    "performance": [
        "Performance Evidence",
        "Cost Friction",
        "Latest Blocks",
        "Repair Actions",
    ],
}


@dataclass(frozen=True)
class JueWikiConfig:
    root_path: Path
    db_path: Path
    enabled: bool = True
    context_max_chars: int = 24000
    page_max_chars: int = 12000
    context_page_limit: int = 8
    kis_blocks_db_path: Path | None = None
    binance_blocks_db_path: Path | None = None
    investment_memory_db_path: Path | None = None
    daily_discovery_db_path: Path | None = None
    trading_validation_db_path: Path | None = None
    jue_codex_lab_db_path: Path | None = None
    naver_reports_db_path: Path | None = None
    symbol_fundamentals_db_path: Path | None = None
    crypto_market_research_db_path: Path | None = None
    market_pulse_db_path: Path | None = None
    etf_research_db_path: Path | None = None
    strategy_insights_db_path: Path | None = None
    crypto_quant_db_path: Path | None = None
    crypto_pattern_lab_db_path: Path | None = None
    crypto_alpha_db_path: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_path", Path(self.root_path))
        object.__setattr__(self, "db_path", Path(self.db_path))
        if self.kis_blocks_db_path is not None:
            object.__setattr__(
                self,
                "kis_blocks_db_path",
                Path(self.kis_blocks_db_path),
            )
        if self.binance_blocks_db_path is not None:
            object.__setattr__(
                self,
                "binance_blocks_db_path",
                Path(self.binance_blocks_db_path),
            )
        if self.investment_memory_db_path is not None:
            object.__setattr__(
                self,
                "investment_memory_db_path",
                Path(self.investment_memory_db_path),
            )
        if self.daily_discovery_db_path is not None:
            object.__setattr__(
                self,
                "daily_discovery_db_path",
                Path(self.daily_discovery_db_path),
            )
        if self.trading_validation_db_path is not None:
            object.__setattr__(
                self,
                "trading_validation_db_path",
                Path(self.trading_validation_db_path),
            )
        if self.jue_codex_lab_db_path is not None:
            object.__setattr__(
                self,
                "jue_codex_lab_db_path",
                Path(self.jue_codex_lab_db_path),
            )
        if self.naver_reports_db_path is not None:
            object.__setattr__(
                self,
                "naver_reports_db_path",
                Path(self.naver_reports_db_path),
            )
        if self.symbol_fundamentals_db_path is not None:
            object.__setattr__(
                self,
                "symbol_fundamentals_db_path",
                Path(self.symbol_fundamentals_db_path),
            )
        if self.crypto_market_research_db_path is not None:
            object.__setattr__(
                self,
                "crypto_market_research_db_path",
                Path(self.crypto_market_research_db_path),
            )
        for attr in (
            "market_pulse_db_path",
            "etf_research_db_path",
            "strategy_insights_db_path",
            "crypto_quant_db_path",
            "crypto_pattern_lab_db_path",
            "crypto_alpha_db_path",
        ):
            value = getattr(self, attr)
            if value is not None:
                object.__setattr__(self, attr, Path(value))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_key(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9가-힣_.-]+", "_", str(raw).strip())
    value = value.strip("._-")
    return value or "unknown"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _metric_value_is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _metric_presence_for(
    metric: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, bool]:
    presence = {"__tracked__": True}
    presence.update(
        {
            key: True
            for key in keys
            if key in metric and _metric_value_is_present(metric.get(key))
        }
    )
    return presence


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_scope(value: str) -> str:
    return _clean_key(value).lower()


def _normalize_page_type(value: str) -> str:
    return _clean_key(value).lower()


def _wiki_freshness_signal(value: Any) -> str:
    freshness = str(value or "").strip().lower()
    if freshness in {"fresh", "current", "recent", "live", "up_to_date"}:
        return "fresh"
    if freshness in {"stale", "old", "expired", "outdated"}:
        return "stale"
    return ""


def _normalize_symbol(value: str) -> str:
    return str(value).strip().upper()


def normalize_jue_wiki_quality_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"strong", "ok", "healthy", "valid", "verified", "complete"}:
        return "strong"
    if status in {"partial", "limited", "sparse", "stale", "incomplete"}:
        return "partial"
    if status in {
        "weak",
        "degraded",
        "error",
        "failed",
        "missing",
        "invalid",
        "broken",
    }:
        return "weak"
    return status


def _normalize_quality_status(value: Any) -> str:
    return normalize_jue_wiki_quality_status(value)


def _is_crypto_tradable_symbol(value: str) -> bool:
    symbol = _normalize_symbol(value)
    return bool(
        re.fullmatch(r"KRW-[A-Z0-9]{1,24}", symbol)
        or re.fullmatch(r"[A-Z0-9]{1,24}(USDT|USDC|FDUSD|BTC|ETH|BNB)", symbol)
    )


class JueWikiSourceReadError(RuntimeError):
    """Raised when a configured source DB exists but cannot be read safely."""


class JueWikiDataIntegrityError(RuntimeError):
    """Raised when Jue Wiki persisted JSON cannot be decoded."""


class JueWikiService:
    def __init__(
        self,
        config: JueWikiConfig,
        *,
        rag_store: Any | None = None,
        etf_research_provider: Any | None = None,
        crypto_market_research_provider: Any | None = None,
    ) -> None:
        self.config = config
        self.rag_store = rag_store
        self.etf_research_provider = etf_research_provider
        self.crypto_market_research_provider = crypto_market_research_provider

    def initialize(self) -> dict[str, Any]:
        self.config.root_path.mkdir(parents=True, exist_ok=True)
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._write_static_file("AGENTS.md", self._agents_md())
        self._write_static_file("schema.md", self._schema_md())
        self._write_static_file(
            "index.md",
            "# Jue Wiki Index\n\nNo compiled pages yet.\n",
        )
        self._write_static_file("log.md", "# Jue Wiki Log\n\n")
        self._write_static_file("freshness.md", "# Jue Wiki Freshness\n\n")
        self._write_static_file(
            "contradictions.md",
            "# Jue Wiki Contradictions\n\n",
        )
        for scope in ("core", "kis", "binance"):
            (self.config.root_path / scope).mkdir(parents=True, exist_ok=True)
        return {
            "status": "ok",
            "root_path": str(self.config.root_path),
            "db_path": str(self.config.db_path),
        }

    def page_id(self, *, scope: str, page_type: str, key: str) -> str:
        return ".".join(
            [
                _normalize_scope(scope),
                _normalize_page_type(page_type),
                _clean_key(key),
            ]
        )

    def page_path(self, *, page_id: str) -> Path:
        parts = page_id.split(".")
        if len(parts) < 3:
            return self.config.root_path / "core" / "unknown.md"
        scope = _normalize_scope(parts[0])
        page_type = _normalize_page_type(parts[1])
        key = _clean_key(".".join(parts[2:]))
        if scope == "core":
            return self.config.root_path / "core" / f"{key}.md"
        directory = {
            "symbol": "symbols",
            "sector": "sectors",
            "regime": "regimes",
            "playbook": "playbooks",
            "lesson": "lessons",
            "core": "core",
        }.get(page_type, f"{page_type}s")
        return self.config.root_path / scope / directory / f"{key}.md"

    def write_page(
        self,
        *,
        scope: str,
        page_type: str,
        key: str,
        title: str,
        symbols: list[str],
        content_sections: dict[str, str],
        source_refs: list[dict[str, Any]],
        confidence: float,
        freshness: str,
    ) -> dict[str, Any]:
        self.initialize()
        clean_scope = _normalize_scope(scope)
        clean_page_type = _normalize_page_type(page_type)
        clean_symbols = [
            _normalize_symbol(symbol)
            for symbol in symbols
            if str(symbol).strip()
        ]
        page_id = self.page_id(scope=clean_scope, page_type=clean_page_type, key=key)
        path = self.page_path(page_id=page_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = _utc_now_iso()
        flattened_source_refs = self._flatten_source_refs(source_refs)
        frontmatter = [
            "---",
            f"scope: {clean_scope}",
            f"page_type: {clean_page_type}",
            f"title: {title}",
            f"symbols: {_json_dumps(clean_symbols)}",
            f"confidence: {float(confidence):.4f}",
            f"freshness: {_clean_key(freshness).lower()}",
            f"last_reviewed_at: {now}",
            f"source_count: {len(flattened_source_refs)}",
            "status: active",
            "---",
            "",
        ]
        body = [f"# {title}", ""]
        for section in self._sections_for(clean_page_type):
            body.append(f"## {section}")
            body.append("")
            body.append(
                content_sections.get(section, "").strip() or "- No current note."
            )
            body.append("")
        content = "\n".join(frontmatter + body)
        if len(content) > self.config.page_max_chars:
            summary_text = content_sections.get("Next Context Pack Summary", "").strip()
            summary_text = summary_text[: max(160, self.config.page_max_chars // 4)]
            summary_block = (
                f"\n\n## Next Context Pack Summary\n\n{summary_text}\n"
                if summary_text
                else ""
            )
            truncation_note = "\n\n## Truncation Note\n\n- Page exceeded budget.\n"
            reserve_chars = len(summary_block) + len(truncation_note) + 24
            head_budget = max(self.config.page_max_chars - reserve_chars, 240)
            content = (
                content[:head_budget].rstrip()
                + truncation_note
                + summary_block
            )
            if len(content) > self.config.page_max_chars:
                content = content[: self.config.page_max_chars].rstrip()
        path.write_text(content, encoding="utf-8")
        content_hash = _hash_text(content)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM wiki_pages WHERE page_id = ?",
                (page_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO wiki_pages (
                    page_id, scope, page_type, title, path, symbols_json,
                    source_refs_json, confidence, freshness, token_estimate,
                    char_count, content_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    page_id,
                    clean_scope,
                    clean_page_type,
                    title,
                    str(path),
                    _json_dumps(clean_symbols),
                    _json_dumps(source_refs),
                    float(confidence),
                    _clean_key(freshness).lower(),
                    max(len(content) // 4, 1),
                    len(content),
                    content_hash,
                    created_at,
                    now,
                ),
            )
            conn.execute("DELETE FROM wiki_source_refs WHERE page_id = ?", (page_id,))
            for ref in flattened_source_refs:
                source_id = self._source_ref_index_id(ref)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO wiki_source_refs (
                        page_id, source_type, source_id, source_path,
                        source_scope, observed_at, content_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        page_id,
                        str(ref.get("source_type") or ""),
                        source_id,
                        str(ref.get("source_path") or ""),
                        str(ref.get("source_scope") or clean_scope),
                        str(ref.get("observed_at") or ""),
                        str(ref.get("content_hash") or ""),
                        now,
                    ),
                )
        return {"status": "ok", "page_id": page_id, "path": str(path)}

    def read_page(self, page_id: str) -> dict[str, Any]:
        path = self.page_path(page_id=page_id)
        if not path.exists():
            return {"status": "not_found", "page_id": page_id, "content": ""}
        return {
            "status": "ok",
            "page_id": page_id,
            "path": str(path),
            "content": path.read_text(encoding="utf-8"),
        }

    def search_pages(
        self,
        *,
        scope: str | None = None,
        symbols: list[str] | None = None,
        page_types: list[str] | None = None,
        include_content: bool = True,
    ) -> list[dict[str, Any]]:
        self.initialize()
        clean_scope = str(scope or "").strip().lower()
        symbol_set = {
            _normalize_symbol(symbol)
            for symbol in symbols or []
            if str(symbol).strip()
        }
        page_type_set = {
            _normalize_page_type(page_type)
            for page_type in page_types or []
            if str(page_type).strip()
        }
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if clean_scope:
            clauses.append("scope = ?")
            params.append(clean_scope)
        if page_type_set:
            placeholders = ",".join(["?"] * len(page_type_set))
            clauses.append(f"page_type IN ({placeholders})")
            params.extend(sorted(page_type_set))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_pages
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, confidence DESC, page_id ASC
                """,
                params,
            ).fetchall()
        pages: list[dict[str, Any]] = []
        for row in rows:
            page_symbols = [
                _normalize_symbol(symbol)
                for symbol in self._parse_json(
                    row["symbols_json"],
                    [],
                    field=f"wiki_pages.symbols_json:{row['page_id']}",
                )
                if str(symbol).strip()
            ]
            if symbol_set and not (set(page_symbols) & symbol_set):
                continue
            path = Path(str(row["path"] or ""))
            content = ""
            if include_content:
                try:
                    content = path.read_text(encoding="utf-8")
                except OSError:
                    content = ""
            pages.append(
                {
                    "page_id": str(row["page_id"]),
                    "scope": str(row["scope"]),
                    "page_type": str(row["page_type"]),
                    "title": str(row["title"]),
                    "symbols": page_symbols,
                    "confidence": float(row["confidence"] or 0.0),
                    "freshness": str(row["freshness"] or "unknown"),
                    "char_count": len(content)
                    if include_content
                    else int(row["char_count"] or 0),
                    "source_refs": self._parse_json(
                        row["source_refs_json"],
                        [],
                        field=f"wiki_pages.source_refs_json:{row['page_id']}",
                    ),
                    "content": content,
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
        return pages

    def search(
        self,
        query: str = "",
        scope: str | None = None,
        page_type: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_query = str(query or "").strip()
        needle = clean_query.lower()
        clean_scope = self._scope_filter_prefix(scope)
        clean_page_type = _normalize_page_type(page_type) if page_type else ""
        pages = self.search_pages(
            scope=clean_scope,
            page_types=[clean_page_type] if clean_page_type else None,
            include_content=False,
        )
        ranked: list[tuple[float, dict[str, Any]]] = []
        for page in pages:
            page_id = str(page.get("page_id") or "")
            title = str(page.get("title") or "")
            symbols = [str(symbol) for symbol in page.get("symbols") or []]
            source_refs = page.get("source_refs")
            summary = self._summary_text(page_id) if page_id else ""
            score = float(page.get("confidence") or 0.0)
            if needle:
                if needle == title.lower() or needle in {
                    symbol.lower() for symbol in symbols
                }:
                    score += 10.0
                elif needle in title.lower():
                    score += 6.0
                elif needle in page_id.lower() or any(
                    needle in symbol.lower() for symbol in symbols
                ):
                    score += 4.0
                elif needle in summary.lower():
                    score += 2.0
                else:
                    continue
            result = {
                key: value for key, value in page.items() if key != "content"
            }
            result["summary"] = summary[:500]
            result["source_count"] = len(self._flatten_source_refs(source_refs))
            result["score"] = round(score, 4)
            ranked.append((score, result))
        ranked.sort(
            key=lambda item: (
                -item[0],
                str(item[1].get("page_id") or ""),
            )
        )
        return [page for _, page in ranked[:50]]

    def record_selection_run(
        self,
        *,
        run_id: str,
        target_scope: str,
        request: dict[str, Any],
        selected_pages: list[dict[str, Any]],
        rejected_pages: list[dict[str, Any]],
        char_count: int,
        max_chars: int,
        status: str,
        budget_report: dict[str, Any] | None = None,
        error_message: str = "",
    ) -> None:
        self.initialize()
        now = _utc_now_iso()
        clean_scope = _normalize_scope(target_scope)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wiki_selection_runs (
                    run_id, target_scope, request_json, budget_report_json, selected_count,
                    rejected_count, char_count, max_chars, status,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    target_scope,
                    _json_dumps(request),
                    _json_dumps(budget_report or {}),
                    len(selected_pages),
                    len(rejected_pages),
                    int(char_count),
                    int(max_chars),
                    status,
                    error_message,
                    now,
                ),
            )
            conn.execute(
                "DELETE FROM wiki_selection_pages WHERE run_id = ?",
                (run_id,),
            )
            for page in selected_pages:
                conn.execute(
                    """
                    INSERT INTO wiki_selection_pages (
                        run_id, page_id, rank, score, reasons_json,
                        penalties_json, char_count, included, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        run_id,
                        str(page.get("page_id") or ""),
                        int(page.get("rank") or 0),
                        float(page.get("score") or 0.0),
                        _json_dumps(page.get("reasons") or []),
                        _json_dumps(page.get("penalties") or []),
                        int(page.get("char_count") or 0),
                        now,
                    ),
                )
            for page in rejected_pages:
                conn.execute(
                    """
                    INSERT INTO wiki_selection_pages (
                        run_id, page_id, rank, score, reasons_json,
                        penalties_json, char_count, included, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        run_id,
                        str(page.get("page_id") or ""),
                        int(page.get("rank") or 0),
                        float(page.get("score") or 0.0),
                        _json_dumps(page.get("reasons") or []),
                        _json_dumps(page.get("penalties") or [page.get("reason")]),
                        int(page.get("char_count") or 0),
                        now,
                    ),
                )
            self._record_requested_symbol_coverage_repairs(
                conn,
                run_id=run_id,
                target_scope=clean_scope,
                budget_report=budget_report or {},
                observed_at=now,
            )

    def _record_requested_symbol_coverage_repairs(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        target_scope: str,
        budget_report: dict[str, Any],
        observed_at: str,
    ) -> None:
        clean_scope = _normalize_scope(target_scope)
        if not clean_scope or not isinstance(budget_report, dict):
            return
        coverage_status = str(
            budget_report.get("requested_symbol_summary_coverage_status") or ""
        ).strip()
        raw_symbols = (
            budget_report.get("requested_symbol_missing_summary_symbols")
            if "requested_symbol_missing_summary_symbols" in budget_report
            else budget_report.get("requested_symbol_unsummarized_symbols")
        )
        symbols = [
            _normalize_symbol(symbol)
            for symbol in list(raw_symbols or [])
            if str(symbol).strip()
        ]
        degraded_reasons = [
            row
            for row in list(
                budget_report.get("requested_symbol_degraded_summary_reasons") or []
            )
            if isinstance(row, dict) and str(row.get("symbol") or "").strip()
        ]
        degraded_symbols = [
            _normalize_symbol(symbol)
            for symbol in list(
                budget_report.get("requested_symbol_degraded_summary_symbols") or []
            )
            if str(symbol).strip()
        ]
        if not degraded_symbols:
            degraded_symbols = [
                _normalize_symbol(row.get("symbol"))
                for row in degraded_reasons
                if str(row.get("symbol") or "").strip()
            ]
        if coverage_status not in {"partial", "none"}:
            coverage_status = "partial" if symbols else coverage_status
        if coverage_status not in {"partial", "none"} and not degraded_symbols:
            return
        if not symbols and not degraded_symbols:
            return
        requested_count = int(budget_report.get("requested_symbol_count") or 0)
        summarized_count = int(
            budget_report.get("requested_symbol_summary_count") or 0
        )
        unsummarized_count = int(
            budget_report.get("requested_symbol_unsummarized_count") or len(symbols)
        )
        missing_count = int(
            budget_report.get("requested_symbol_missing_summary_count") or len(symbols)
        )
        for symbol in dict.fromkeys(symbols):
            page_id = self.page_id(scope=clean_scope, page_type="symbol", key=symbol)
            action_id = f"repair:coverage:{clean_scope}:{symbol}"
            finding_id = f"requested_symbol_coverage:{clean_scope}:{symbol}"
            existing = conn.execute(
                """
                SELECT created_at
                FROM wiki_repair_actions
                WHERE action_id = ?
                """,
                (action_id,),
            ).fetchone()
            created_at = (
                str(existing["created_at"] or observed_at)
                if existing is not None
                else observed_at
            )
            details = {
                "decision_scope": clean_scope,
                "selection_run_id": str(run_id),
                "coverage_status": coverage_status,
                "symbols": [symbol],
                "impacted_symbols": [symbol],
                "impacted_page_ids": [page_id],
                "quality_warnings": ["requested_symbol_summary_missing"],
                "requested_symbol_count": requested_count,
                "summarized_symbol_count": summarized_count,
                "unsummarized_symbol_count": unsummarized_count,
                "missing_symbol_count": missing_count,
                "repair_action": "collect_or_rebuild_requested_symbol_wiki_summary",
                "reasons": [
                    f"selection_run:{run_id}",
                    f"coverage_status:{coverage_status}",
                    "requested_symbol_missing_from_context_pack",
                ],
                "repair_targets": [
                    {
                        "page_id": page_id,
                        "symbol": symbol,
                        "recommended_action": (
                            "collect_or_rebuild_requested_symbol_wiki_summary"
                        ),
                    }
                ],
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, '', '')
                """,
                (
                    action_id,
                    finding_id,
                    page_id,
                    "refresh_requested_symbol_summary",
                    _json_dumps(details),
                    created_at,
                ),
            )
        for reason in degraded_reasons:
            symbol = _normalize_symbol(reason.get("symbol"))
            if not symbol or symbol not in degraded_symbols:
                continue
            page_id = self.page_id(scope=clean_scope, page_type="symbol", key=symbol)
            action_id = f"repair:degraded_summary:{clean_scope}:{symbol}"
            finding_id = f"requested_symbol_degraded_summary:{clean_scope}:{symbol}"
            existing = conn.execute(
                """
                SELECT created_at
                FROM wiki_repair_actions
                WHERE action_id = ?
                """,
                (action_id,),
            ).fetchone()
            created_at = (
                str(existing["created_at"] or observed_at)
                if existing is not None
                else observed_at
            )
            quality_warnings = ["requested_symbol_summary_degraded"]
            for warning in list(reason.get("quality_warnings") or []):
                clean_warning = str(warning).strip()
                if clean_warning and clean_warning not in quality_warnings:
                    quality_warnings.append(clean_warning)
            freshness_warnings = [
                str(warning).strip()
                for warning in list(reason.get("freshness_warnings") or [])
                if str(warning).strip()
            ]
            for warning in freshness_warnings:
                if warning not in quality_warnings:
                    quality_warnings.append(warning)
            quality_status = _normalize_quality_status(reason.get("quality_status"))
            details = {
                "decision_scope": clean_scope,
                "selection_run_id": str(run_id),
                "coverage_status": coverage_status or "full",
                "symbols": [symbol],
                "impacted_symbols": [symbol],
                "impacted_page_ids": [page_id],
                "quality_status": quality_status,
                "freshness": str(reason.get("freshness") or ""),
                "freshness_status": str(reason.get("freshness_status") or ""),
                "freshness_warnings": freshness_warnings,
                "quality_warnings": quality_warnings,
                "requested_symbol_count": requested_count,
                "summarized_symbol_count": summarized_count,
                "degraded_symbol_count": int(
                    budget_report.get("requested_symbol_degraded_summary_count")
                    or len(degraded_symbols)
                ),
                "repair_action": (
                    "refresh_stale_or_weak_requested_symbol_wiki_summary"
                ),
                "reasons": [
                    f"selection_run:{run_id}",
                    "requested_symbol_summary_degraded",
                    f"freshness:{reason.get('freshness') or 'unknown'}",
                    (
                        "freshness_status:"
                        f"{reason.get('freshness_status') or 'unknown'}"
                    ),
                    f"quality_status:{quality_status or 'unknown'}",
                ],
                "repair_targets": [
                    {
                        "page_id": page_id,
                        "symbol": symbol,
                        "recommended_action": (
                            "refresh_stale_or_weak_requested_symbol_wiki_summary"
                        ),
                    }
                ],
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, '', '')
                """,
                (
                    action_id,
                    finding_id,
                    page_id,
                    "refresh_requested_symbol_summary",
                    _json_dumps(details),
                    created_at,
                ),
            )

    def _record_manager_observation_repair_actions(
        self,
        *,
        scope: str,
        observations_by_symbol: dict[str, list[dict[str, Any]]],
    ) -> int:
        clean_scope = _normalize_scope(scope)
        if not clean_scope or not observations_by_symbol:
            return 0
        now = _utc_now_iso()
        written = 0
        with self._connect() as conn:
            for raw_symbol, observations in observations_by_symbol.items():
                symbol = _normalize_symbol(raw_symbol)
                if not symbol:
                    continue
                page_id = self.page_id(
                    scope=clean_scope,
                    page_type="symbol",
                    key=symbol,
                )
                for row in list(observations or [])[:8]:
                    if not isinstance(row, dict):
                        continue
                    action_type = str(
                        row.get("wiki_attention_action_type")
                        or row.get("wiki_attention_recommended")
                        or ""
                    ).strip()
                    if not action_type:
                        continue
                    manager_run_id = str(row.get("manager_run_id") or "").strip()
                    action_id = (
                        f"repair:manager_context:{clean_scope}:"
                        f"{_clean_key(action_type)}:{symbol}"
                    )
                    finding_id = (
                        f"manager_context_repair:{clean_scope}:"
                        f"{_clean_key(action_type)}:{symbol}"
                    )
                    existing = conn.execute(
                        """
                        SELECT created_at
                        FROM wiki_repair_actions
                        WHERE action_id = ?
                        """,
                        (action_id,),
                    ).fetchone()
                    created_at = (
                        str(existing["created_at"] or now)
                        if existing is not None
                        else now
                    )
                    warnings = [
                        str(item).strip()
                        for item in list(
                            row.get("wiki_evidence_quality_warnings") or []
                        )[:8]
                        if str(item).strip()
                    ]
                    if not warnings:
                        warnings = ["manager_context_repair_required"]
                    evidence_counts = (
                        row.get("wiki_evidence_quality_status_counts")
                        if isinstance(
                            row.get("wiki_evidence_quality_status_counts"), dict
                        )
                        else {}
                    )
                    recommended_action = str(
                        row.get("wiki_attention_recommended") or action_type
                    )
                    details = {
                        "decision_scope": clean_scope,
                        "source_type": "prompt.jue_wiki_selection_observation",
                        "source_id": manager_run_id,
                        "source_status": str(
                            row.get("wiki_evidence_quality_summary") or ""
                        ),
                        "manager_run_id": manager_run_id,
                        "manager_observed_at": str(row.get("observed_at") or ""),
                        "symbols": [symbol],
                        "impacted_symbols": [symbol],
                        "impacted_page_ids": [page_id],
                        "action_type": action_type,
                        "quality_warnings": list(dict.fromkeys(warnings)),
                        "evidence_quality_summary": str(
                            row.get("wiki_evidence_quality_summary") or ""
                        ),
                        "evidence_quality_status_counts": evidence_counts,
                        "repair_action": recommended_action,
                        "reasons": [
                            f"manager_run:{manager_run_id or '-'}",
                            "prompt.jue_wiki_selection_observation",
                            "manager_context_repair_batch",
                        ],
                        "repair_targets": [
                            {
                                "page_id": page_id,
                                "symbol": symbol,
                                "recommended_action": recommended_action,
                            }
                        ],
                        "requires_manager_confirmation": True,
                    }
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO wiki_repair_actions (
                            action_id, finding_id, page_id, action_type, status,
                            details_json, created_at, finished_at, error_message
                        ) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, '', '')
                        """,
                        (
                            action_id,
                            finding_id,
                            page_id,
                            action_type,
                            _json_dumps(details),
                            created_at,
                        ),
                    )
                    written += 1
        return written

    def _resolve_manager_observation_repair_actions(
        self,
        *,
        scope: str,
        observations_by_symbol: dict[str, list[dict[str, Any]]],
    ) -> int:
        clean_scope = _normalize_scope(scope)
        if not clean_scope or not observations_by_symbol:
            return 0
        now = _utc_now_iso()
        resolved = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, page_id, action_type, details_json
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
            for row in rows:
                action_id = str(row["action_id"] or "")
                details = self._parse_json(
                    row["details_json"],
                    {},
                    field=f"wiki_repair_actions.details_json:{action_id}",
                    allow_missing=True,
                )
                if not isinstance(details, dict):
                    continue
                if not bool(details.get("requires_manager_confirmation")):
                    continue
                detail_scope = str(
                    details.get("decision_scope")
                    or details.get("scope")
                    or details.get("source_scope")
                    or ""
                ).strip().lower()
                if detail_scope and detail_scope != clean_scope:
                    continue
                symbols = self._repair_queue_symbols_for_action(
                    page_id=str(row["page_id"] or ""),
                    details=details,
                    scope=clean_scope,
                )
                original_at = self._parse_datetime(
                    str(details.get("manager_observed_at") or "")
                )
                resolved_row: dict[str, Any] | None = None
                resolved_symbol = ""
                for symbol in symbols:
                    for observation in list(observations_by_symbol.get(symbol) or []):
                        if not self._manager_observation_evidence_quality_is_clean(
                            observation
                        ):
                            continue
                        observed_at = self._parse_datetime(
                            str(observation.get("observed_at") or "")
                        )
                        if (
                            original_at is not None
                            and observed_at is not None
                            and observed_at <= original_at
                        ):
                            continue
                        resolved_row = observation
                        resolved_symbol = symbol
                        break
                    if resolved_row is not None:
                        break
                if resolved_row is None:
                    continue
                resolved_details = {
                    **details,
                    "resolved_by": "manager_context_evidence_quality_recovered",
                    "resolved_at": now,
                    "resolved_symbol": resolved_symbol,
                    "resolved_manager_run_id": str(
                        resolved_row.get("manager_run_id") or ""
                    ),
                    "resolved_manager_observed_at": str(
                        resolved_row.get("observed_at") or ""
                    ),
                    "resolved_evidence_quality_summary": str(
                        resolved_row.get("wiki_evidence_quality_summary") or ""
                    ),
                    "resolved_evidence_quality_status_counts": (
                        resolved_row.get("wiki_evidence_quality_status_counts")
                        if isinstance(
                            resolved_row.get("wiki_evidence_quality_status_counts"),
                            dict,
                        )
                        else {}
                    ),
                    "resolved_warnings": list(details.get("quality_warnings") or []),
                }
                conn.execute(
                    """
                    UPDATE wiki_repair_actions
                    SET status = 'resolved',
                        finished_at = CASE
                            WHEN COALESCE(finished_at, '') = '' THEN ?
                            ELSE finished_at
                        END,
                        details_json = ?
                    WHERE action_id = ?
                      AND status IN ('scheduled', 'unresolved')
                    """,
                    (
                        now,
                        _json_dumps(resolved_details),
                        action_id,
                    ),
                )
                resolved += 1
        return resolved

    @classmethod
    def _manager_observation_evidence_quality_is_clean(
        cls,
        row: dict[str, Any],
    ) -> bool:
        if not isinstance(row, dict):
            return False
        warnings = [
            str(item).strip()
            for item in list(row.get("wiki_evidence_quality_warnings") or [])
            if str(item).strip()
        ]
        if warnings:
            return False
        counts = (
            row.get("wiki_evidence_quality_status_counts")
            if isinstance(row.get("wiki_evidence_quality_status_counts"), dict)
            else {}
        )
        if not counts:
            return False
        strong_count = 0
        for raw_status, raw_count in counts.items():
            status = _normalize_quality_status(raw_status)
            count = int(cls._manager_number(raw_count) or 0)
            if count <= 0:
                continue
            if status == "strong":
                strong_count += count
            else:
                return False
        return strong_count > 0

    def status(self) -> dict[str, Any]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT scope, COUNT(*) AS count
                FROM wiki_pages
                WHERE status = 'active'
                GROUP BY scope
                """
            ).fetchall()
            health = self._status_health(conn)
        scopes = {str(row["scope"]): int(row["count"]) for row in rows}
        from tradecraft.services.jue_wiki_application import (
            JueWikiApplicationService,
        )

        return {
            "status": "ok",
            "enabled": bool(self.config.enabled),
            "root_path": str(self.config.root_path),
            "db_path": str(self.config.db_path),
            "page_count": sum(scopes.values()),
            "scopes": scopes,
            "research_coverage": self._research_coverage_status(),
            "application": JueWikiApplicationService(self).status(),
            **health,
        }

    def _research_coverage_status(self) -> dict[str, Any]:
        by_scope: dict[str, Any] = {}
        unhealthy_source_ids: list[str] = []
        total_warning_count = 0
        for scope in ("kis", "binance"):
            rows = self._research_coverage_rows(scope=scope)
            sources: dict[str, Any] = {}
            configured_count = 0
            ok_count = 0
            warning_count = 0
            for row in rows:
                source_id = str(row.get("source_id") or "").strip()
                if not source_id:
                    continue
                status = str(row.get("status") or "unknown").strip().lower()
                configured = status != "missing_path"
                table_status_counts: dict[str, int] = {}
                for detail in list(row.get("tables") or []):
                    if not isinstance(detail, dict):
                        continue
                    table_status = str(detail.get("status") or "unknown").strip()
                    if table_status:
                        table_status_counts[table_status] = (
                            table_status_counts.get(table_status, 0) + 1
                        )
                table_warning_count = sum(
                    count
                    for table_status, count in table_status_counts.items()
                    if table_status != "ok"
                )
                warning = configured and (status != "ok" or table_warning_count > 0)
                if configured:
                    configured_count += 1
                if status == "ok":
                    ok_count += 1
                if warning:
                    warning_count += 1
                    unhealthy_source_ids.append(f"{scope}.{source_id}")
                sources[source_id] = {
                    "status": status,
                    "configured": configured,
                    "warning": warning,
                    "reason": str(row.get("reason") or ""),
                    "rows": int(row.get("rows") or 0),
                    "symbols": int(row.get("symbols") or 0),
                    "latest_at": str(row.get("latest_at") or ""),
                    "primary_table": str(row.get("primary_table") or ""),
                    "path": str(row.get("path") or ""),
                    "table_status_counts": {
                        key: table_status_counts[key]
                        for key in sorted(table_status_counts)
                    },
                }
            total_warning_count += warning_count
            by_scope[scope] = {
                "source_count": len(rows),
                "configured_count": configured_count,
                "ok_count": ok_count,
                "warning_count": warning_count,
                "sources": sources,
            }
        return {
            "by_scope": by_scope,
            "warning_count": total_warning_count,
            "unhealthy_source_ids": unhealthy_source_ids[:64],
        }

    def _status_health(self, conn: sqlite3.Connection) -> dict[str, Any]:
        active_page_count = self._optional_count(
            conn,
            table="wiki_pages",
            sql="SELECT COUNT(*) FROM wiki_pages WHERE status = 'active'",
        )
        stale_page_count = self._stale_page_count(conn)
        open_lint_count = self._optional_count(
            conn,
            table="wiki_lint_findings",
            sql="""
                SELECT COUNT(*)
                FROM wiki_lint_findings
                WHERE status = 'open'
            """,
        )
        latest_selection = self._latest_selection_status(conn)
        full_selection_min_chars = max(int(self.config.context_max_chars or 0), 1)
        latest_full_selection = self._latest_selection_status(
            conn,
            min_max_chars=full_selection_min_chars,
        )
        latest_compact_selection = self._latest_selection_status(
            conn,
            max_max_chars=full_selection_min_chars - 1,
        )
        pressure_selection = latest_full_selection or latest_selection
        latest_repair = self._latest_repair_status(conn)
        repair_queue = self._repair_queue_status(conn)
        optional_tables_missing = [
            table
            for table in (
                "wiki_pages",
                "wiki_lint_findings",
                "wiki_selection_runs",
                "wiki_repair_actions",
            )
            if not self._table_exists(conn, table)
        ]
        return {
            "active_page_count": active_page_count,
            "open_lint_count": open_lint_count,
            "stale_page_count": stale_page_count,
            "latest_selection": latest_selection,
            "latest_full_selection": latest_full_selection,
            "latest_compact_selection": latest_compact_selection,
            "last_selection_at": str(latest_selection.get("created_at") or ""),
            "latest_repair": latest_repair,
            "repair_queue": repair_queue,
            "wiki_repair_queue_open_count": int(
                repair_queue.get("open_count") or 0
            ),
            "wiki_repair_queue_resolved_count": int(
                repair_queue.get("resolved_count") or 0
            ),
            "last_repair_at": str(
                latest_repair.get("finished_at")
                or latest_repair.get("created_at")
                or ""
            ),
            "prompt_pressure": {
                "char_count": int(pressure_selection.get("char_count") or 0),
                "max_chars": int(pressure_selection.get("max_chars") or 0),
            },
            "compact_prompt_pressure": {
                "char_count": int(latest_compact_selection.get("char_count") or 0),
                "max_chars": int(latest_compact_selection.get("max_chars") or 0),
            },
            "optional_tables_missing": optional_tables_missing,
        }

    def _stale_page_count(self, conn: sqlite3.Connection) -> int:
        if not self._table_exists(conn, "wiki_pages"):
            return 0
        rows = conn.execute(
            """
            SELECT freshness, updated_at
            FROM wiki_pages
            WHERE status = 'active'
            """
        ).fetchall()
        return sum(1 for row in rows if self._is_stale_page(row))

    def _context_page_sort_key(
        self,
        row: sqlite3.Row,
        *,
        target_scope: str,
    ) -> tuple[int, int, float, float, str]:
        scope = str(row["scope"] or "")
        scope_rank = 0 if scope == target_scope else 1 if scope == "core" else 2
        freshness_signal = _wiki_freshness_signal(row["freshness"])
        freshness_rank = 1
        if freshness_signal == "stale" or self._is_stale_page(row):
            freshness_rank = 2
        elif freshness_signal == "fresh":
            freshness_rank = 0
        try:
            confidence = float(row["confidence"] or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        updated_at = self._parse_datetime(str(row["updated_at"] or ""))
        updated_ts = updated_at.timestamp() if updated_at is not None else 0.0
        return (
            scope_rank,
            freshness_rank,
            -confidence,
            -updated_ts,
            str(row["page_id"] or ""),
        )

    def _page_freshness_profile(self, row: sqlite3.Row) -> dict[str, Any]:
        freshness = str(row["freshness"] or "").strip().lower()
        signal = _wiki_freshness_signal(freshness)
        warnings: list[str] = []
        updated_at = self._parse_datetime(str(row["updated_at"] or ""))
        age_stale = (
            updated_at is not None
            and datetime.now(timezone.utc) - updated_at > timedelta(days=14)
        )
        if signal == "stale":
            status = "stale"
            warnings.append("freshness_label_stale")
        elif age_stale:
            status = "stale"
            warnings.append("updated_at_stale_gt_14d")
        elif signal == "fresh":
            status = "fresh"
        else:
            status = "unknown"
            warnings.append("freshness_unknown")
        return {
            "freshness_status": status,
            "freshness_warnings": warnings,
        }

    @staticmethod
    def _merge_freshness_profiles(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        warning_counts: dict[str, int] = {}
        stale_page_ids: list[str] = []
        unknown_page_ids: list[str] = []
        for row in rows:
            page_id = str(row.get("page_id") or "").strip()
            status = str(row.get("freshness_status") or "unknown").strip().lower()
            if not status:
                status = "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "stale" and page_id:
                stale_page_ids.append(page_id)
            if status == "unknown" and page_id:
                unknown_page_ids.append(page_id)
            for warning in list(row.get("freshness_warnings") or []):
                clean_warning = str(warning or "").strip()
                if clean_warning:
                    warning_counts[clean_warning] = (
                        warning_counts.get(clean_warning, 0) + 1
                    )
        return {
            "page_count": len(rows),
            "status_counts": {
                key: status_counts[key] for key in sorted(status_counts)
            },
            "warning_counts": {
                key: warning_counts[key] for key in sorted(warning_counts)
            },
            "stale_page_ids": stale_page_ids[:12],
            "unknown_page_ids": unknown_page_ids[:12],
        }

    def _repair_queue_status(self, conn: sqlite3.Connection) -> dict[str, Any]:
        if not self._table_exists(conn, "wiki_repair_actions"):
            return {"open_count": 0, "resolved_count": 0, "by_scope": {}}
        rows = conn.execute(
            """
            SELECT page_id, action_type, status, details_json, COUNT(*) AS count
            FROM wiki_repair_actions
            GROUP BY page_id, action_type, status, details_json
            """
        ).fetchall()
        by_scope: dict[str, dict[str, int]] = {}
        open_by_action_type: dict[str, int] = {}
        open_by_warning: dict[str, int] = {}
        open_symbols: set[str] = set()
        open_batch_rows: list[dict[str, Any]] = []
        open_count = 0
        resolved_count = 0
        for row in rows:
            page_id = str(row["page_id"] or "")
            status = str(row["status"] or "")
            count = int(row["count"] or 0)
            action_type = str(row["action_type"] or "")
            details = self._parse_json(
                row["details_json"],
                {},
                field=f"wiki_repair_actions.details_json:{page_id}:{action_type}",
                allow_missing=True,
            )
            if not isinstance(details, dict):
                details = {}
            detail_scope = str(
                details.get("decision_scope")
                or details.get("scope")
                or details.get("source_scope")
                or ""
            ).strip().lower()
            scope = (
                detail_scope
                if detail_scope
                else page_id.split(".", 1)[0]
                if "." in page_id
                else "unknown"
            )
            scope_row = by_scope.setdefault(
                scope,
                {"open_count": 0, "resolved_count": 0},
            )
            if status in {"scheduled", "unresolved"}:
                open_count += count
                scope_row["open_count"] += count
                if action_type:
                    open_by_action_type[action_type] = (
                        open_by_action_type.get(action_type, 0) + count
                    )
                if isinstance(details, dict):
                    for warning in list(details.get("quality_warnings") or []):
                        clean_warning = str(warning).strip()
                        if clean_warning:
                            open_by_warning[clean_warning] = (
                                open_by_warning.get(clean_warning, 0) + count
                            )
                    symbols = self._repair_queue_symbols_for_action(
                        page_id=page_id,
                        details=details,
                        scope=scope,
                    )
                    open_symbols.update(symbols)
                    open_batch_rows.append(
                        {
                            "scope": scope,
                            "page_id": page_id,
                            "action_type": action_type,
                            "details": details,
                            "symbols": symbols,
                            "count": count,
                        }
                    )
            elif status == "resolved":
                resolved_count += count
                scope_row["resolved_count"] += count
        return {
            "open_count": open_count,
            "resolved_count": resolved_count,
            "by_scope": {
                key: by_scope[key]
                for key in sorted(by_scope)
            },
            "open_by_action_type": {
                key: open_by_action_type[key]
                for key in sorted(open_by_action_type)
            },
            "open_by_warning": {
                key: open_by_warning[key]
                for key in sorted(open_by_warning)
            },
            "open_symbols": sorted(open_symbols)[:64],
            "open_action_batches": self._repair_queue_action_batches(
                open_batch_rows,
            ),
        }

    @staticmethod
    def _repair_queue_symbols_for_action(
        *,
        page_id: str,
        details: dict[str, Any],
        scope: str,
    ) -> list[str]:
        symbols = [
            _normalize_symbol(symbol)
            for symbol in [
                *list(details.get("symbols") or []),
                *list(details.get("impacted_symbols") or []),
            ]
            if str(symbol).strip()
        ]
        for target in list(details.get("repair_targets") or []):
            if not isinstance(target, dict):
                continue
            clean_symbol = _normalize_symbol(str(target.get("symbol") or ""))
            if clean_symbol:
                symbols.append(clean_symbol)
        symbol_prefix = f"{scope}.symbol."
        page_symbol = (
            page_id.rsplit(".", 1)[-1]
            if page_id.startswith(symbol_prefix) and "." in page_id
            else ""
        )
        if page_symbol:
            symbols.append(_normalize_symbol(page_symbol))
        return [
            symbol
            for symbol in dict.fromkeys(symbols)
            if str(symbol).strip()
        ]

    @staticmethod
    def _repair_queue_recommended_actions(
        details: dict[str, Any],
    ) -> list[str]:
        recommended: list[str] = []
        for target in list(details.get("repair_targets") or []):
            if not isinstance(target, dict):
                continue
            action = str(target.get("recommended_action") or "").strip()
            if action:
                recommended.append(action)
        return [
            action
            for action in dict.fromkeys(recommended)
            if action
        ]

    def _repair_queue_action_batches(
        self,
        rows: list[dict[str, Any]],
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            action_type = str(row.get("action_type") or "").strip()
            if not action_type:
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            scope = str(row.get("scope") or "").strip().lower()
            if not scope:
                page_id = str(row.get("page_id") or "")
                scope = page_id.split(".", 1)[0] if "." in page_id else "unknown"
            key = (scope, action_type)
            batch = grouped.setdefault(
                key,
                {
                    "scope": scope,
                    "action_type": action_type,
                    "count": 0,
                    "symbols": set(),
                    "warnings": set(),
                    "recommended_actions": set(),
                },
            )
            batch["count"] += int(row.get("count") or 1)
            batch["symbols"].update(
                str(symbol)
                for symbol in list(row.get("symbols") or [])
                if str(symbol).strip()
            )
            batch["warnings"].update(
                str(warning).strip()
                for warning in list(details.get("quality_warnings") or [])
                if str(warning).strip()
            )
            batch["recommended_actions"].update(
                self._repair_queue_recommended_actions(details)
            )
        batches: list[dict[str, Any]] = []
        for batch in grouped.values():
            batches.append(
                {
                    "scope": batch["scope"],
                    "action_type": batch["action_type"],
                    "count": int(batch["count"]),
                    "symbols": sorted(batch["symbols"])[:64],
                    "warnings": sorted(batch["warnings"])[:16],
                    "recommended_actions": sorted(
                        batch["recommended_actions"]
                    )[:16],
                }
            )
        return sorted(
            batches,
            key=lambda row: (
                str(row.get("scope") or ""),
                str(row.get("action_type") or ""),
            ),
        )[: max(int(limit), 0)]

    def _context_pack_repair_queue(
        self,
        *,
        scope: str,
        symbols: set[str],
    ) -> dict[str, Any]:
        clean_scope = _normalize_scope(scope)
        if not clean_scope:
            return {
                "open_count": 0,
                "resolved_count": 0,
                "open_symbols": [],
                "open_action_batches": [],
            }
        rows = self._repair_queue_rows(scope=clean_scope)
        open_rows: list[dict[str, Any]] = []
        resolved_count = 0
        for row in rows:
            status = str(row.get("status") or "")
            row_symbols = {
                _normalize_symbol(symbol)
                for symbol in list(row.get("symbols") or [])
                if str(symbol).strip()
            }
            if symbols and not (row_symbols & symbols):
                continue
            if status in {"scheduled", "unresolved"}:
                open_rows.append(
                    {
                        **row,
                        "scope": clean_scope,
                        "count": 1,
                        "symbols": sorted(row_symbols),
                    }
                )
            elif status == "resolved":
                resolved_count += 1
        open_rows = sorted(open_rows, key=self._repair_queue_open_row_sort_key)
        open_symbols = sorted(
            {
                str(symbol)
                for row in open_rows
                for symbol in list(row.get("symbols") or [])
                if str(symbol).strip()
            }
        )
        return {
            "open_count": len(open_rows),
            "resolved_count": resolved_count,
            "open_symbols": open_symbols[:64],
            "open_action_batches": self._repair_queue_action_batches(
                open_rows,
                limit=8,
            ),
            "open_actions": [
                {
                    "action_id": str(row.get("action_id") or ""),
                    "page_id": str(row.get("page_id") or ""),
                    "action_type": str(row.get("action_type") or ""),
                    "symbols": list(row.get("symbols") or [])[:12],
                    "quality_warnings": [
                        str(warning)
                        for warning in list(
                            dict(row.get("details") or {}).get(
                                "quality_warnings"
                            )
                            or []
                        )[:8]
                        if str(warning).strip()
                    ],
                    "repair_action": str(
                        dict(row.get("details") or {}).get("repair_action") or ""
                    ),
                }
                for row in open_rows[:8]
            ],
        }

    @staticmethod
    def _context_pack_repair_queue_chunk(repair_queue: dict[str, Any]) -> str:
        open_count = int(repair_queue.get("open_count") or 0)
        if open_count <= 0:
            return ""
        lines = [
            "### Jue Wiki Repair Queue",
            f"- open_count={open_count}",
        ]
        open_symbols = [
            str(symbol)
            for symbol in list(repair_queue.get("open_symbols") or [])[:16]
            if str(symbol).strip()
        ]
        if open_symbols:
            lines.append(f"- open_symbols={','.join(open_symbols)}")
        for batch in list(repair_queue.get("open_action_batches") or [])[:6]:
            if not isinstance(batch, dict):
                continue
            symbols = ",".join(
                str(symbol)
                for symbol in list(batch.get("symbols") or [])[:12]
                if str(symbol).strip()
            )
            warnings = ",".join(
                str(warning)
                for warning in list(batch.get("warnings") or [])[:8]
                if str(warning).strip()
            )
            recommended = ",".join(
                str(action)
                for action in list(batch.get("recommended_actions") or [])[:8]
                if str(action).strip()
            )
            lines.append(
                "- action_type={action_type}, count={count}, symbols={symbols}, "
                "warnings={warnings}, recommended_actions={recommended}".format(
                    action_type=batch.get("action_type") or "-",
                    count=batch.get("count") or 0,
                    symbols=symbols or "-",
                    warnings=warnings or "-",
                    recommended=recommended or "-",
                )
            )
        return "\n".join(lines)

    def _optional_count(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        sql: str,
    ) -> int:
        if not self._table_exists(conn, table):
            return 0
        return int(conn.execute(sql).fetchone()[0] or 0)

    def _latest_selection_status(
        self,
        conn: sqlite3.Connection,
        *,
        min_max_chars: int | None = None,
        max_max_chars: int | None = None,
    ) -> dict[str, Any]:
        if not self._table_exists(conn, "wiki_selection_runs"):
            return {}
        filters: list[str] = []
        params: list[Any] = []
        if min_max_chars is not None:
            filters.append("max_chars >= ?")
            params.append(int(min_max_chars))
        if max_max_chars is not None:
            filters.append("max_chars <= ?")
            params.append(int(max_max_chars))
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        row = conn.execute(
            f"""
            SELECT run_id, target_scope, selected_count, rejected_count,
                   char_count, max_chars, status, error_message, created_at,
                   budget_report_json
            FROM wiki_selection_runs
            {where_sql}
            ORDER BY created_at DESC, run_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return {}
        return {
            "run_id": str(row["run_id"]),
            "target_scope": str(row["target_scope"]),
            "selected_count": int(row["selected_count"] or 0),
            "rejected_count": int(row["rejected_count"] or 0),
            "char_count": int(row["char_count"] or 0),
            "max_chars": int(row["max_chars"] or 0),
            "status": str(row["status"]),
            "error_message": str(row["error_message"] or ""),
            "created_at": str(row["created_at"] or ""),
            "budget_report": self._parse_json(row["budget_report_json"], {}),
        }

    def _latest_repair_status(self, conn: sqlite3.Connection) -> dict[str, Any]:
        if not self._table_exists(conn, "wiki_repair_actions"):
            return {}
        row = conn.execute(
            """
            SELECT action_id, finding_id, page_id, action_type, status,
                   created_at, finished_at, error_message
            FROM wiki_repair_actions
            ORDER BY COALESCE(NULLIF(finished_at, ''), created_at) DESC,
                     action_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return {}
        return {
            "action_id": str(row["action_id"]),
            "finding_id": str(row["finding_id"]),
            "page_id": str(row["page_id"]),
            "action_type": str(row["action_type"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "error_message": str(row["error_message"] or ""),
        }

    def _resolve_repair_actions_for_clean_findings(
        self,
        conn: sqlite3.Connection,
        resolved_at: str,
    ) -> None:
        if not self._table_exists(conn, "wiki_repair_actions"):
            return
        if not self._table_exists(conn, "wiki_lint_findings"):
            return
        conn.execute(
            """
            UPDATE wiki_repair_actions
            SET status = 'resolved',
                finished_at = CASE
                    WHEN COALESCE(finished_at, '') = '' THEN ?
                    ELSE finished_at
                END
            WHERE status IN ('unresolved', 'scheduled')
              AND EXISTS (
                  SELECT 1
                  FROM wiki_lint_findings AS f
                  WHERE f.finding_id = wiki_repair_actions.finding_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM wiki_lint_findings AS f
                  WHERE f.finding_id = wiki_repair_actions.finding_id
                    AND f.status = 'open'
              )
            """,
            (resolved_at,),
        )

    def _resolve_repair_actions_for_clean_targets(
        self,
        conn: sqlite3.Connection,
        resolved_at: str,
    ) -> None:
        if not self._table_exists(conn, "wiki_repair_actions"):
            return
        if not self._table_exists(conn, "wiki_pages"):
            return
        rows = conn.execute(
            """
            SELECT action_id, details_json
            FROM wiki_repair_actions
            WHERE status IN ('unresolved', 'scheduled')
            ORDER BY created_at ASC, action_id ASC
            """
        ).fetchall()
        for row in rows:
            action_id = str(row["action_id"] or "")
            details = self._parse_json(
                row["details_json"],
                {},
                field=f"wiki_repair_actions.details_json:{action_id}",
                allow_missing=True,
            )
            if not isinstance(details, dict):
                continue
            if bool(details.get("requires_manager_confirmation")):
                continue
            warnings = {
                str(item).strip()
                for item in list(details.get("quality_warnings") or [])
                if str(item).strip()
            }
            target_page_ids = self._repair_action_target_page_ids(details)
            if not warnings or not target_page_ids:
                continue
            if not self._repair_action_targets_are_clean(
                conn,
                target_page_ids=target_page_ids,
                warnings=warnings,
            ):
                continue
            resolved_details = {
                **details,
                "resolved_by": "repair_targets_cleaned",
                "resolved_at": resolved_at,
                "resolved_warnings": sorted(warnings),
                "resolved_target_page_ids": target_page_ids,
            }
            conn.execute(
                """
                UPDATE wiki_repair_actions
                SET status = 'resolved',
                    finished_at = CASE
                        WHEN COALESCE(finished_at, '') = '' THEN ?
                        ELSE finished_at
                    END,
                    details_json = ?
                WHERE action_id = ?
                  AND status IN ('unresolved', 'scheduled')
                """,
                (
                    resolved_at,
                    _json_dumps(resolved_details),
                    action_id,
                ),
            )

    @staticmethod
    def _repair_action_target_page_ids(details: dict[str, Any]) -> list[str]:
        page_ids: list[str] = []
        for item in list(details.get("impacted_page_ids") or []):
            page_id = str(item).strip()
            if page_id and page_id not in page_ids:
                page_ids.append(page_id)
        for target in list(details.get("repair_targets") or []):
            if not isinstance(target, dict):
                continue
            page_id = str(target.get("page_id") or "").strip()
            if page_id and page_id not in page_ids:
                page_ids.append(page_id)
        return page_ids[:24]

    def _repair_action_targets_are_clean(
        self,
        conn: sqlite3.Connection,
        *,
        target_page_ids: list[str],
        warnings: set[str],
    ) -> bool:
        for page_id in target_page_ids:
            row = conn.execute(
                """
                SELECT freshness, updated_at, source_refs_json
                FROM wiki_pages
                WHERE page_id = ? AND status = 'active'
                LIMIT 1
                """,
                (page_id,),
            ).fetchone()
            if row is None:
                return False
            if "requested_symbol_summary_degraded" in warnings:
                freshness = str(row["freshness"] or "").strip().lower()
                if _wiki_freshness_signal(freshness) != "fresh":
                    return False
                if self._is_stale_page(row):
                    return False
            refs = self._parse_json(
                row["source_refs_json"],
                [],
                field=f"wiki_pages.source_refs_json:{page_id}",
                allow_missing=True,
            )
            if not isinstance(refs, list):
                return False
            if "requested_symbol_summary_missing" in warnings and not refs:
                return False
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                ref_warnings = {
                    str(item).strip()
                    for item in list(ref.get("quality_warnings") or [])
                    if str(item).strip()
                }
                if ref_warnings & warnings:
                    return False
                if "requested_symbol_summary_degraded" in warnings:
                    quality_status = _normalize_quality_status(
                        ref.get("quality_status")
                    )
                    if quality_status in {"weak", "partial", "unknown"}:
                        return False
        return True

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table,),
        ).fetchone()
        return row is not None

    def context_pack(
        self,
        *,
        target_scope: str = "",
        symbols: list[str] | None = None,
        page_types: list[str] | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        budget = max(int(max_chars or self.config.context_max_chars), 0)
        clean_scope = str(target_scope or "").strip().lower()
        symbol_set = {
            _normalize_symbol(symbol)
            for symbol in symbols or []
            if str(symbol).strip()
        }
        page_type_set = {
            _normalize_page_type(page_type)
            for page_type in page_types or []
            if str(page_type).strip()
        }
        rows = self._select_context_pages(
            target_scope=clean_scope,
            symbols=symbol_set,
            page_types=page_type_set,
            limit=self.config.context_page_limit,
        )
        open_repair_actions_by_page = self._context_pack_open_repair_actions_by_page(
            scope=clean_scope,
        )
        pages: list[dict[str, Any]] = []
        chunks: list[str] = []
        evidence_quality_rows: list[dict[str, Any]] = []
        freshness_profile_rows: list[dict[str, Any]] = []
        used = 0
        for row in rows:
            page_id = str(row["page_id"])
            source_refs = self._parse_json(
                row["source_refs_json"],
                [],
                field=f"wiki_pages.source_refs_json:{page_id}",
                allow_missing=True,
            )
            if not isinstance(source_refs, list):
                source_refs = []
            evidence_quality = self.source_refs_quality_summary(source_refs)
            open_repair_actions = list(open_repair_actions_by_page.get(page_id) or [])
            if open_repair_actions:
                evidence_quality = (
                    self._context_pack_evidence_quality_with_open_repair_actions(
                        evidence_quality=evidence_quality,
                        open_repair_actions=open_repair_actions,
                    )
                )
            freshness_profile = self._page_freshness_profile(row)
            chunk = self._extract_context_summary(page_id)
            if not chunk:
                continue
            freshness_status = str(
                freshness_profile.get("freshness_status") or ""
            ).strip()
            freshness_warnings = [
                str(item).strip()
                for item in list(freshness_profile.get("freshness_warnings") or [])
                if str(item).strip()
            ]
            if freshness_status and freshness_status != "fresh":
                warning_suffix = (
                    f"; warnings={','.join(freshness_warnings)}"
                    if freshness_warnings
                    else ""
                )
                chunk = f"{chunk}\n- freshness_status={freshness_status}{warning_suffix}"
            quality_line = str(evidence_quality.get("summary_line") or "")
            if quality_line:
                chunk = f"{chunk}\n- {quality_line}"
            separator_len = 2 if chunks else 0
            next_len = separator_len + len(chunk)
            if budget and used + next_len > budget:
                remaining = budget - used - separator_len
                if remaining <= 0:
                    break
                suffix = "\n- Context truncated by budget."
                if remaining <= len(suffix):
                    chunk = suffix[:remaining]
                else:
                    chunk = chunk[: remaining - len(suffix)].rstrip() + suffix
                next_len = separator_len + len(chunk)
            elif not budget:
                break
            chunks.append(chunk)
            pages.append(
                {
                    "page_id": page_id,
                    "scope": row["scope"],
                    "page_type": row["page_type"],
                    "title": row["title"],
                    "confidence": row["confidence"],
                    "freshness": row["freshness"],
                    **freshness_profile,
                    "summary": self._summary_text(page_id),
                    "evidence_quality": evidence_quality,
                }
            )
            freshness_profile_rows.append(
                {
                    "page_id": page_id,
                    **freshness_profile,
                }
            )
            if evidence_quality:
                evidence_quality_rows.append(evidence_quality)
            used += next_len
            if used >= budget:
                break
        repair_queue = self._context_pack_repair_queue(
            scope=clean_scope,
            symbols=symbol_set,
        )
        repair_chunk = self._context_pack_repair_queue_chunk(repair_queue)
        if repair_chunk and budget and used < budget:
            separator_len = 2 if chunks else 0
            next_len = separator_len + len(repair_chunk)
            if used + next_len > budget:
                remaining = budget - used - separator_len
                suffix = "\n- Context truncated by budget."
                if remaining > len(suffix):
                    repair_chunk = (
                        repair_chunk[: remaining - len(suffix)].rstrip() + suffix
                    )
                    next_len = separator_len + len(repair_chunk)
                else:
                    repair_chunk = ""
            if repair_chunk:
                chunks.append(repair_chunk)
                used += next_len
        content = "\n\n".join(chunks)
        evidence_quality = self.merge_evidence_quality(evidence_quality_rows)
        return {
            "status": "ok",
            "target_scope": clean_scope or "all",
            "symbols": sorted(symbol_set),
            "page_types": sorted(page_type_set),
            "pages": pages,
            "repair_queue": repair_queue,
            "repair_action_batches": list(
                repair_queue.get("open_action_batches") or []
            ),
            "evidence_quality": evidence_quality,
            "evidence_quality_summary": evidence_quality.get("summary_line", ""),
            "freshness_summary": self._merge_freshness_profiles(
                freshness_profile_rows
            ),
            "content": content,
            "char_count": len(content),
            "budget": budget,
        }

    def _context_pack_open_repair_actions_by_page(
        self,
        *,
        scope: str,
    ) -> dict[str, list[dict[str, Any]]]:
        self.initialize()
        clean_scope = str(scope or "").strip().lower()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        by_page: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            page_id = str(row["page_id"] or "").strip()
            details = self._parse_json(
                row["details_json"],
                {},
                field=f"wiki_repair_actions.details_json:{row['action_id']}",
                allow_missing=True,
            )
            if not isinstance(details, dict):
                details = {}
            decision_scope = str(
                details.get("decision_scope")
                or details.get("scope")
                or details.get("source_scope")
                or ""
            ).strip().lower()
            if clean_scope and not (
                page_id.startswith(f"{clean_scope}.") or decision_scope == clean_scope
            ):
                continue
            quality_warnings = [
                str(item).strip()
                for item in list(details.get("quality_warnings") or [])[:8]
                if str(item).strip()
            ]
            by_page.setdefault(page_id, []).append(
                {
                    "action_id": str(row["action_id"] or ""),
                    "finding_id": str(row["finding_id"] or ""),
                    "page_id": page_id,
                    "action_type": str(row["action_type"] or ""),
                    "status": str(row["status"] or ""),
                    "quality_warnings": quality_warnings,
                    "repair_action": str(details.get("repair_action") or "")[:240],
                }
            )
        return by_page

    def _context_pack_evidence_quality_with_open_repair_actions(
        self,
        *,
        evidence_quality: dict[str, Any],
        open_repair_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        open_count = len(open_repair_actions)
        warning_counts: dict[str, int] = {"open_repair_queue": open_count}
        for action in open_repair_actions:
            for warning in list(action.get("quality_warnings") or []):
                clean_warning = str(warning or "").strip()
                if clean_warning:
                    warning_counts[clean_warning] = (
                        warning_counts.get(clean_warning, 0) + 1
                    )
        repair_quality = {
            "source_count": open_count,
            "status_counts": {"partial": open_count},
            "warning_counts": warning_counts,
            "source_type_counts": {"wiki_repair_actions": open_count},
        }
        merged = self.merge_evidence_quality([evidence_quality, repair_quality])
        merged["repair_queue"] = {
            "open_count": open_count,
            "actions": [
                self._context_pack_compact_open_repair_action(action)
                for action in open_repair_actions[:6]
            ],
        }
        return merged

    @staticmethod
    def _context_pack_compact_open_repair_action(
        action: dict[str, Any],
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "action_type": str(action.get("action_type") or "")[:120],
            "status": str(action.get("status") or "")[:40],
        }
        quality_warnings = [
            str(item).strip()[:120]
            for item in list(action.get("quality_warnings") or [])[:6]
            if str(item).strip()
        ]
        if quality_warnings:
            row["quality_warnings"] = quality_warnings
        return row

    def rebuild(self, *, scope: str = "", force: bool = False) -> dict[str, Any]:
        _ = force
        self.initialize()
        clean_scope = str(scope or "").strip().lower()
        scopes = ["kis", "binance"] if clean_scope in {"", "all"} else [clean_scope]
        updated_count = 0
        warnings: list[str] = self._source_readiness_warnings(scopes)
        errors: list[str] = []
        for item_scope in scopes:
            try:
                if item_scope == "kis":
                    updated_count += self._rebuild_manager_run_ops_page(scope="kis")
                    self._rebuild_action_pressure_page(scope="kis")
                    self._rebuild_opportunity_pipeline_page(scope="kis")
                    updated_count += self._rebuild_trading_validation_risk_page(
                        scope="kis"
                    )
                    updated_count += self._rebuild_codex_lab_green_path_page(
                        scope="kis"
                    )
                    self._rebuild_research_coverage_page(scope="kis")
                    self._rebuild_repair_queue_page(scope="kis")
                    updated_count += self._rebuild_market_pulse_regime_page()
                    updated_count += self._rebuild_kis_symbols()
                    updated_count += self._rebuild_evidence_quality_page(scope="kis")
                    self._resolve_repair_actions_after_rebuild()
                    self._rebuild_repair_queue_page(scope="kis")
                elif item_scope == "binance":
                    updated_count += self._rebuild_manager_run_ops_page(
                        scope="binance"
                    )
                    self._rebuild_action_pressure_page(scope="binance")
                    self._rebuild_opportunity_pipeline_page(scope="binance")
                    updated_count += self._rebuild_trading_validation_risk_page(
                        scope="binance"
                    )
                    updated_count += self._rebuild_codex_lab_green_path_page(
                        scope="binance"
                    )
                    self._rebuild_research_coverage_page(scope="binance")
                    self._rebuild_repair_queue_page(scope="binance")
                    updated_count += self._rebuild_binance_symbols()
                    updated_count += self._rebuild_evidence_quality_page(
                        scope="binance"
                    )
                    self._resolve_repair_actions_after_rebuild()
                    self._rebuild_repair_queue_page(scope="binance")
                else:
                    warnings.append(f"unsupported_scope:{item_scope}")
            except JueWikiSourceReadError as exc:
                errors.append(str(exc))
        status = "error" if errors else "warn" if warnings else "ok"
        self._record_run(
            kind="rebuild",
            scope=clean_scope or "all",
            status=status,
            updated_count=updated_count,
            warning_count=len(warnings),
            error_message="; ".join(errors),
        )
        return {
            "status": status,
            "scope": clean_scope or "all",
            "updated_count": updated_count,
            "warnings": warnings,
            "errors": errors,
            "error_message": "; ".join(errors),
        }

    def _resolve_repair_actions_after_rebuild(self) -> None:
        now = _utc_now_iso()
        with self._connect() as conn:
            self._resolve_repair_actions_for_clean_targets(conn, now)

    def _source_readiness_warnings(self, scopes: list[str]) -> list[str]:
        warnings: list[str] = []
        checks = {
            "kis": self.config.kis_blocks_db_path,
            "binance": self.config.binance_blocks_db_path,
        }
        for scope in scopes:
            path = checks.get(scope)
            if path is None:
                continue
            source_path = Path(path)
            if not source_path.exists():
                warnings.append(f"missing_{scope}_blocks_db:{source_path}")
                continue
            try:
                with sqlite3.connect(source_path) as conn:
                    if not self._table_exists(conn, "blocks"):
                        warnings.append(f"missing_{scope}_blocks_table:{source_path}")
            except JueWikiSourceReadError as exc:
                warnings.append(str(exc))
            except sqlite3.DatabaseError as exc:
                warnings.append(f"failed_to_inspect_{scope}_blocks_db:{source_path}:{exc}")
        return warnings

    def source_refs_quality_summary(
        self,
        source_refs: list[dict[str, Any]] | Any,
    ) -> dict[str, Any]:
        refs = source_refs if isinstance(source_refs, list) else []
        status_counts: dict[str, int] = {}
        warning_counts: dict[str, int] = {}
        typed_counts: dict[str, int] = {}
        total = 0

        def safe_count(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        def quality_status_rank(status: str) -> int:
            return {
                "strong": 0,
                "unknown": 1,
                "partial": 2,
                "weak": 3,
            }.get(status, 1)

        def status_counts_total(value: Any) -> int:
            if not isinstance(value, dict):
                return 0
            return sum(safe_count(count) for count in value.values())

        def single_status_from_counts(value: Any) -> str:
            if not isinstance(value, dict):
                return ""
            statuses = [
                _normalize_quality_status(status)
                for status, count in value.items()
                if safe_count(count) > 0 and _normalize_quality_status(status)
            ]
            return statuses[0] if len(statuses) == 1 else ""

        def status_counts_delta(before: dict[str, int]) -> dict[str, int]:
            keys = set(before) | set(status_counts)
            return {
                key: status_counts.get(key, 0) - before.get(key, 0)
                for key in keys
                if status_counts.get(key, 0) - before.get(key, 0) > 0
            }

        def replace_single_status(old_status: str, new_status: str) -> None:
            if old_status:
                next_count = status_counts.get(old_status, 0) - 1
                if next_count > 0:
                    status_counts[old_status] = next_count
                else:
                    status_counts.pop(old_status, None)
            if new_status:
                status_counts[new_status] = status_counts.get(new_status, 0) + 1

        def add_counts(
            target: dict[str, int],
            source: Any,
            *,
            normalize_quality_status: bool = False,
        ) -> None:
            if not isinstance(source, dict):
                return
            for key, count in source.items():
                clean_key = (
                    _normalize_quality_status(key)
                    if normalize_quality_status
                    else str(key or "").strip()
                )
                clean_count = safe_count(count)
                if clean_key and clean_count > 0:
                    target[clean_key] = target.get(clean_key, 0) + clean_count

        def add_nested_summary(summary: Any) -> bool:
            nonlocal total
            if not isinstance(summary, dict):
                return False
            nested_total = safe_count(summary.get("source_count"))
            nested_status_counts = summary.get("status_counts")
            nested_warning_counts = summary.get("warning_counts")
            nested_type_counts = summary.get("source_type_counts")
            nested_has_quality = bool(
                nested_total
                or nested_status_counts
                or nested_warning_counts
                or nested_type_counts
                or summary.get("top_warnings")
            )
            if nested_total <= 0 and isinstance(nested_status_counts, dict):
                nested_total = sum(
                    safe_count(count) for count in nested_status_counts.values()
                )
            if nested_total <= 0 and nested_has_quality:
                nested_total = 1
            if nested_total > 0:
                total += nested_total
            add_counts(
                status_counts,
                nested_status_counts,
                normalize_quality_status=True,
            )
            add_counts(warning_counts, nested_warning_counts)
            add_counts(typed_counts, nested_type_counts)
            for item in list(summary.get("top_warnings") or []):
                if isinstance(item, dict):
                    warning = str(item.get("warning") or "").strip()
                    count = safe_count(item.get("count"))
                else:
                    warning = str(item).strip()
                    count = 1
                if warning and count > 0 and warning not in warning_counts:
                    warning_counts[warning] = count
            return nested_has_quality

        def visit(ref: dict[str, Any], *, depth: int) -> None:
            nonlocal total
            if depth > 3:
                return
            if not isinstance(ref, dict):
                return
            status = _normalize_quality_status(ref.get("quality_status"))
            warnings = [
                str(item).strip()
                for item in list(ref.get("quality_warnings") or [])
                if str(item).strip()
            ]
            nested_quality = (
                ref.get("evidence_quality")
                if isinstance(ref.get("evidence_quality"), dict)
                else {}
            )
            nested_has_quality = False
            if nested_quality:
                nested_total = safe_count(nested_quality.get("source_count"))
                nested_status_counts = nested_quality.get("status_counts")
                nested_warning_counts = nested_quality.get("warning_counts")
                nested_type_counts = nested_quality.get("source_type_counts")
                if nested_total <= 0 and isinstance(nested_status_counts, dict):
                    nested_total = sum(
                        safe_count(count)
                        for count in nested_status_counts.values()
                    )
                nested_has_quality = bool(
                    nested_total
                    or nested_status_counts
                    or nested_warning_counts
                    or nested_type_counts
                    or nested_quality.get("top_warnings")
                )
                effective_nested_quality = nested_quality
                nested_count_total = status_counts_total(nested_status_counts)
                nested_status = single_status_from_counts(nested_status_counts)
                if (
                    status in {"partial", "weak"}
                    and nested_status
                    and nested_count_total == 1
                    and quality_status_rank(status) > quality_status_rank(nested_status)
                ):
                    effective_nested_quality = {
                        **nested_quality,
                        "status_counts": {status: 1},
                    }
                    nested_status_counts = effective_nested_quality["status_counts"]
                add_nested_summary(effective_nested_quality)
                if nested_has_quality:
                    if status and not isinstance(nested_status_counts, dict):
                        status_counts[status] = status_counts.get(status, 0) + 1
                    if not isinstance(nested_type_counts, dict):
                        source_type = (
                            str(ref.get("source_type") or "unknown").strip()
                            or "unknown"
                        )
                        typed_counts[source_type] = (
                            typed_counts.get(source_type, 0) + 1
                        )
                    for warning in warnings:
                        if warning and warning not in warning_counts:
                            warning_counts[warning] = 1
                    return
            nested_refs = ref.get("source_refs")
            nested_contributed_quality = False
            if isinstance(nested_refs, list):
                before_total = total
                before_status_counts = dict(status_counts)
                for nested_ref in nested_refs:
                    if isinstance(nested_ref, dict):
                        visit(nested_ref, depth=depth + 1)
                nested_contributed_quality = total > before_total
                contributed_count = total - before_total
                if nested_contributed_quality and contributed_count == 1:
                    nested_status = single_status_from_counts(
                        status_counts_delta(before_status_counts)
                    )
                    if (
                        status in {"partial", "weak"}
                        and nested_status
                        and quality_status_rank(status)
                        > quality_status_rank(nested_status)
                    ):
                        replace_single_status(nested_status, status)
            if nested_contributed_quality:
                for warning in warnings:
                    if warning and warning not in warning_counts:
                        warning_counts[warning] = 1
                return
            has_source_identity = bool(
                str(ref.get("source_type") or "").strip()
                or str(ref.get("source_id") or "").strip()
            )
            if not status and not warnings and not has_source_identity:
                return
            total += 1
            status = status or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            source_type = str(ref.get("source_type") or "unknown").strip() or "unknown"
            typed_counts[source_type] = typed_counts.get(source_type, 0) + 1
            for warning in warnings:
                warning_counts[warning] = warning_counts.get(warning, 0) + 1

        for ref in refs:
            if isinstance(ref, dict):
                visit(ref, depth=0)
        if total <= 0:
            return {}
        top_warnings = sorted(
            warning_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:6]
        status_order = ("strong", "partial", "weak", "unknown")
        status_text = ", ".join(
            f"{status}={status_counts.get(status, 0)}"
            for status in status_order
            if status_counts.get(status, 0)
        )
        warning_text = ", ".join(
            f"{warning}:{count}" for warning, count in top_warnings
        )
        summary_line = "evidence_quality sources={total}, {statuses}".format(
            total=total,
            statuses=status_text or "status=unknown",
        )
        if warning_text:
            summary_line = f"{summary_line}, warnings={warning_text}"
        return {
            "source_count": total,
            "status_counts": status_counts,
            "warning_counts": warning_counts,
            "source_type_counts": typed_counts,
            "top_warnings": [
                {"warning": warning, "count": count}
                for warning, count in top_warnings
            ],
            "summary_line": summary_line,
        }

    @staticmethod
    def _flatten_source_refs(
        source_refs: list[dict[str, Any]] | Any,
        *,
        max_depth: int = 3,
    ) -> list[dict[str, Any]]:
        refs = source_refs if isinstance(source_refs, list) else []
        rows: list[dict[str, Any]] = []

        def visit(items: list[Any], *, depth: int) -> None:
            if depth > max(int(max_depth), 0):
                return
            for ref in items:
                if not isinstance(ref, dict):
                    continue
                rows.append(ref)
                nested_refs = ref.get("source_refs")
                if isinstance(nested_refs, list):
                    visit(nested_refs, depth=depth + 1)

        visit(refs, depth=0)
        return rows

    @staticmethod
    def _source_ref_index_id(ref: dict[str, Any]) -> str:
        source_id = str(ref.get("source_id") or "").strip()
        if source_id:
            return source_id
        source_type = _clean_key(str(ref.get("source_type") or "unknown")).lower()
        digest = _hash_text(_json_dumps(ref))[:16]
        return f"generated:{source_type}:{digest}"

    @staticmethod
    def _quality_status_from_evidence_quality(evidence_quality: Any) -> str:
        if not isinstance(evidence_quality, dict):
            return ""
        status_counts = (
            evidence_quality.get("status_counts")
            if isinstance(evidence_quality.get("status_counts"), dict)
            else {}
        )
        canonical_counts: dict[str, int] = {}
        for raw_status, raw_count in status_counts.items():
            status = _normalize_quality_status(raw_status)
            if not status:
                continue
            try:
                count = int(raw_count or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                canonical_counts[status] = canonical_counts.get(status, 0) + count
        for status in ("weak", "partial", "unknown", "strong"):
            try:
                count = int(canonical_counts.get(status) or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                return status
        return ""

    @staticmethod
    def _quality_warnings_from_evidence_quality(
        evidence_quality: Any,
        *,
        limit: int = 8,
    ) -> list[str]:
        if not isinstance(evidence_quality, dict):
            return []
        warnings: list[str] = []

        def add_warning(value: Any) -> None:
            warning = str(value or "").strip()
            if warning and warning not in warnings:
                warnings.append(warning)

        for item in list(evidence_quality.get("top_warnings") or []):
            if isinstance(item, dict):
                add_warning(item.get("warning"))
            else:
                add_warning(item)
            if len(warnings) >= max(int(limit), 0):
                return warnings
        warning_counts = (
            evidence_quality.get("warning_counts")
            if isinstance(evidence_quality.get("warning_counts"), dict)
            else {}
        )
        def warning_count(item: tuple[Any, Any]) -> tuple[int, str]:
            try:
                count = int(item[1] or 0)
            except (TypeError, ValueError):
                count = 0
            return (-count, str(item[0] or ""))

        for warning, count in sorted(
            warning_counts.items(),
            key=warning_count,
        ):
            try:
                clean_count = int(count or 0)
            except (TypeError, ValueError):
                clean_count = 0
            if clean_count > 0:
                add_warning(warning)
            if len(warnings) >= max(int(limit), 0):
                break
        return warnings

    def merge_evidence_quality(
        self,
        rows: list[dict[str, Any]] | Any,
    ) -> dict[str, Any]:
        items = rows if isinstance(rows, list) else []
        status_counts: dict[str, int] = {}
        warning_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        total = 0
        for row in items:
            if not isinstance(row, dict):
                continue
            total += int(row.get("source_count") or 0)
            for key, value in dict(row.get("status_counts") or {}).items():
                clean_status = _normalize_quality_status(key)
                if clean_status:
                    status_counts[clean_status] = status_counts.get(
                        clean_status, 0
                    ) + int(value or 0)
            for key, value in dict(row.get("warning_counts") or {}).items():
                warning_counts[str(key)] = warning_counts.get(str(key), 0) + int(value or 0)
            for key, value in dict(row.get("source_type_counts") or {}).items():
                type_counts[str(key)] = type_counts.get(str(key), 0) + int(value or 0)
        if total <= 0:
            return {}
        top_warnings = sorted(
            warning_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:8]
        status_order = ("strong", "partial", "weak", "unknown")
        status_text = ", ".join(
            f"{status}={status_counts.get(status, 0)}"
            for status in status_order
            if status_counts.get(status, 0)
        )
        warning_text = ", ".join(
            f"{warning}:{count}" for warning, count in top_warnings
        )
        summary_line = "evidence_quality_total sources={total}, {statuses}".format(
            total=total,
            statuses=status_text or "status=unknown",
        )
        if warning_text:
            summary_line = f"{summary_line}, warnings={warning_text}"
        return {
            "source_count": total,
            "status_counts": status_counts,
            "warning_counts": warning_counts,
            "source_type_counts": type_counts,
            "top_warnings": [
                {"warning": warning, "count": count}
                for warning, count in top_warnings
            ],
            "summary_line": summary_line,
        }

    def lint(self, *, scope: str = "") -> dict[str, Any]:
        self.initialize()
        selected_scope = self._scope_filter_prefix(scope)
        now = _utc_now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_pages
                WHERE status = 'active' AND (? = '' OR scope = ?)
                ORDER BY scope, page_id
                """,
                (selected_scope, selected_scope),
            ).fetchall()
            if selected_scope:
                page_ids = [str(row["page_id"]) for row in rows]
                if page_ids:
                    placeholders = ",".join(["?"] * len(page_ids))
                    conn.execute(
                        f"""
                        UPDATE wiki_lint_findings
                        SET status = 'resolved', resolved_at = ?
                        WHERE status = 'open' AND page_id IN ({placeholders})
                        """,
                        [now, *page_ids],
                    )
            else:
                conn.execute(
                    """
                    UPDATE wiki_lint_findings
                    SET status = 'resolved', resolved_at = ?
                    WHERE status = 'open'
                    """,
                    (now,),
                )
            findings: list[dict[str, Any]] = []
            for row in rows:
                findings.extend(self._lint_page(row))
            for finding in findings:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO wiki_lint_findings (
                        finding_id, page_id, severity, finding_type, message,
                        evidence_json, status, created_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, '')
                    """,
                    (
                        finding["finding_id"],
                        finding["page_id"],
                        finding["severity"],
                        finding["finding_type"],
                        finding["message"],
                        _json_dumps(finding.get("evidence") or {}),
                        now,
                    ),
                )
            self._resolve_repair_actions_for_clean_findings(conn, now)
            self._resolve_repair_actions_for_clean_targets(conn, now)
        return {
            "status": "warn" if findings else "ok",
            "scope": selected_scope or "all",
            "open_findings": findings,
        }

    def list_lint_findings(
        self,
        *,
        scope: str | None = None,
        status: str = "open",
    ) -> list[dict[str, Any]]:
        self.initialize()
        selected_scope = self._scope_filter_prefix(scope)
        clean_status = str(status or "").strip()
        clauses: list[str] = []
        params: list[Any] = []
        if selected_scope:
            clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM wiki_pages AS p
                    WHERE p.page_id = f.page_id AND p.scope = ?
                )
                """
            )
            params.append(selected_scope)
        if clean_status and clean_status.lower() != "all":
            clauses.append("f.status = ?")
            params.append(clean_status)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT f.*
                FROM wiki_lint_findings AS f
                {where_sql}
                ORDER BY created_at DESC, page_id ASC, finding_id ASC
                """,
                params,
            ).fetchall()
        return [
            {
                "finding_id": str(row["finding_id"]),
                "page_id": str(row["page_id"]),
                "severity": str(row["severity"]),
                "finding_type": str(row["finding_type"]),
                "message": str(row["message"]),
                "evidence": self._parse_json(
                    row["evidence_json"],
                    {},
                    field=f"wiki_lint_findings.evidence_json:{row['finding_id']}",
                ),
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
                "resolved_at": str(row["resolved_at"] or ""),
            }
            for row in rows
        ]

    def record_repair_action(
        self,
        *,
        finding_id: str,
        page_id: str,
        action_type: str,
        status: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        self.initialize()
        now = _utc_now_iso()
        action_id = _hash_text(
            f"{finding_id}:{page_id}:{action_type}:{status}:{now}"
        )[:32]
        action = {
            "action_id": action_id,
            "finding_id": finding_id,
            "page_id": page_id,
            "action_type": action_type,
            "status": status,
            "details": details,
            "created_at": now,
            "finished_at": "",
            "error_message": "",
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wiki_repair_actions (
                    action_id, finding_id, page_id, action_type, status,
                    details_json, created_at, finished_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '')
                """,
                (
                    action_id,
                    finding_id,
                    page_id,
                    action_type,
                    status,
                    _json_dumps(details),
                    now,
                ),
            )
        return action

    def upsert_playbook_metric(self, metric: dict[str, Any]) -> None:
        self.initialize()
        page_id = str(metric.get("page_id") or "").strip()
        scope = str(metric.get("scope") or "").strip().lower()
        playbook_id = str(metric.get("playbook_id") or "").strip()
        if not page_id or not scope or not playbook_id:
            raise ValueError("page_id, scope, and playbook_id are required")
        now = _utc_now_iso()
        metric_keys = (
            "sample_count",
            "win_rate",
            "expectancy",
            "profit_factor",
            "max_drawdown_pct",
            "avg_holding_minutes",
        )
        metric_presence = _metric_presence_for(metric, metric_keys)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wiki_playbook_metrics (
                    page_id, scope, playbook_id, sample_count, win_rate,
                    expectancy, profit_factor, max_drawdown_pct,
                    avg_holding_minutes, status, reasons_json,
                    metric_presence_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    scope,
                    playbook_id,
                    int(metric.get("sample_count") or 0),
                    float(metric.get("win_rate") or 0.0),
                    float(metric.get("expectancy") or 0.0),
                    float(metric.get("profit_factor") or 0.0),
                    float(metric.get("max_drawdown_pct") or 0.0),
                    float(metric.get("avg_holding_minutes") or 0.0),
                    str(metric.get("status") or "probe"),
                    _json_dumps(metric.get("reasons") or []),
                    _json_dumps(metric_presence),
                    now,
                ),
            )

    def playbook_metric(self, page_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM wiki_playbook_metrics
                WHERE page_id = ?
                """,
                (page_id,),
            ).fetchone()
        if row is None:
            return {"status": "not_found", "page_id": page_id}
        metric = {
            "page_id": str(row["page_id"]),
            "scope": str(row["scope"]),
            "playbook_id": str(row["playbook_id"]),
            "sample_count": int(row["sample_count"] or 0),
            "win_rate": float(row["win_rate"] or 0.0),
            "expectancy": float(row["expectancy"] or 0.0),
            "profit_factor": float(row["profit_factor"] or 0.0),
            "max_drawdown_pct": float(row["max_drawdown_pct"] or 0.0),
            "avg_holding_minutes": float(row["avg_holding_minutes"] or 0.0),
            "status": str(row["status"] or "probe"),
            "reasons": self._parse_json(
                row["reasons_json"],
                [],
                field=f"wiki_playbook_metrics.reasons_json:{row['page_id']}",
            ),
            "updated_at": str(row["updated_at"] or ""),
        }
        presence = self._parse_json(
            row["metric_presence_json"],
            {},
            field=f"wiki_playbook_metrics.metric_presence_json:{row['page_id']}",
        )
        if isinstance(presence, dict) and presence.get("__tracked__"):
            for key in (
                "sample_count",
                "win_rate",
                "expectancy",
                "profit_factor",
                "max_drawdown_pct",
                "avg_holding_minutes",
            ):
                if not presence.get(key):
                    metric.pop(key, None)
        return metric

    def upsert_page_effectiveness(self, metric: dict[str, Any]) -> None:
        self.initialize()
        page_id = str(metric.get("page_id") or "").strip()
        decision_scope = str(metric.get("decision_scope") or "").strip().lower()
        if not page_id or not decision_scope:
            raise ValueError("page_id and decision_scope are required")
        now = _utc_now_iso()
        reasons = metric.get("reasons_json")
        if reasons is None:
            reasons = metric.get("reasons") or []
        metric_keys = (
            "sample_count",
            "win_rate",
            "expectancy",
            "avg_return_pct",
            "median_mae_pct",
            "drawdown_pressure",
            "helpful_score",
            "confidence",
        )
        metric_presence = _metric_presence_for(metric, metric_keys)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO wiki_page_effectiveness (
                    page_id, decision_scope, venue, horizon, sample_count,
                    win_rate, expectancy, avg_return_pct, median_mae_pct,
                    drawdown_pressure, helpful_score, confidence, status,
                    reasons_json, metric_presence_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    decision_scope,
                    str(metric.get("venue") or ""),
                    str(metric.get("horizon") or ""),
                    int(metric.get("sample_count") or 0),
                    float(metric.get("win_rate") or 0.0),
                    float(metric.get("expectancy") or 0.0),
                    float(metric.get("avg_return_pct") or 0.0),
                    float(metric.get("median_mae_pct") or 0.0),
                    float(metric.get("drawdown_pressure") or 0.0),
                    float(metric.get("helpful_score") or 0.0),
                    float(metric.get("confidence") or 0.0),
                    str(metric.get("status") or "probe"),
                    _json_dumps(reasons),
                    _json_dumps(metric_presence),
                    str(metric.get("updated_at") or now),
                ),
            )

    def page_effectiveness_map(
        self,
        *,
        decision_scope: str,
        horizons: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        self.initialize()
        clean_scope = str(decision_scope or "").strip().lower()
        clean_horizons = [
            str(horizon).strip().lower()
            for horizon in list(horizons or [])
            if str(horizon).strip()
        ]
        horizon_clause = ""
        params: list[Any] = [clean_scope, clean_scope]
        if clean_horizons:
            placeholders = ",".join("?" for _ in clean_horizons)
            horizon_clause = f"AND (horizon = '' OR horizon IN ({placeholders}))"
            params.extend(clean_horizons)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_page_effectiveness
                WHERE (? = '' OR decision_scope = ?)
                  {horizon_clause}
                ORDER BY updated_at DESC
                """,
                params,
            ).fetchall()
        by_page: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_page.setdefault(str(row["page_id"]), []).append(
                self._page_effectiveness_row_to_metric(dict(row))
            )
        horizon_set = set(clean_horizons)

        def rows_for_prompt(page_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not horizon_set:
                return page_rows
            specific_rows = [
                row
                for row in page_rows
                if str(row.get("horizon") or "").strip().lower() in horizon_set
            ]
            if specific_rows:
                return specific_rows
            return [
                {
                    **row,
                    "requested_horizons": list(clean_horizons),
                    "fallback_reason": "general_horizon_metric",
                }
                for row in page_rows
            ]

        return {
            page_id: self._aggregate_page_effectiveness_rows(
                rows_for_prompt(page_rows)
            )
            for page_id, page_rows in by_page.items()
        }

    def _page_effectiveness_row_to_metric(
        self,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        presence = self._parse_json(
            row.get("metric_presence_json"),
            {},
            field=f"wiki_page_effectiveness.metric_presence_json:{row.get('page_id')}",
        )
        if isinstance(presence, dict) and presence.get("__tracked__"):
            row["metric_presence"] = {
                str(key): bool(value)
                for key, value in presence.items()
                if str(key) != "__tracked__"
            }
        return row

    def _aggregate_page_effectiveness_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rows:
            return {}
        if len(rows) == 1:
            return dict(rows[0])
        sample_count = sum(int(row.get("sample_count") or 0) for row in rows)

        def weighted_average(key: str) -> float:
            if sample_count <= 0:
                return sum(float(row.get(key) or 0.0) for row in rows) / len(rows)
            return (
                sum(
                    float(row.get(key) or 0.0)
                    * int(row.get("sample_count") or 0)
                    for row in rows
                )
                / sample_count
            )

        degraded_rows = [
            row for row in rows if str(row.get("status") or "") == "degraded"
        ]
        active_rows = [row for row in rows if str(row.get("status") or "") == "active"]
        if degraded_rows:
            status = "degraded"
            anchor = min(degraded_rows, key=lambda row: float(row.get("helpful_score") or 0.0))
            helpful_score = min(float(row.get("helpful_score") or 0.0) for row in rows)
        elif active_rows:
            status = "active"
            anchor = max(active_rows, key=lambda row: float(row.get("helpful_score") or 0.0))
            helpful_score = max(float(row.get("helpful_score") or 0.0) for row in rows)
        else:
            status = str(rows[0].get("status") or "probe")
            anchor = rows[0]
            helpful_score = weighted_average("helpful_score")
        tracked_presence_rows = [
            row.get("metric_presence")
            for row in rows
            if isinstance(row.get("metric_presence"), dict)
        ]
        metric_presence: dict[str, bool] = {}
        if len(tracked_presence_rows) == len(rows):
            for key in (
                "sample_count",
                "win_rate",
                "expectancy",
                "avg_return_pct",
                "median_mae_pct",
                "drawdown_pressure",
                "helpful_score",
                "confidence",
            ):
                if any(
                    bool(presence.get(key))
                    for presence in tracked_presence_rows
                    if isinstance(presence, dict)
                ):
                    metric_presence[key] = True

        horizon_status = [
            f"{str(row.get('horizon') or 'all')}:{str(row.get('status') or 'probe')}"
            f":n{int(row.get('sample_count') or 0)}"
            f":score{float(row.get('helpful_score') or 0.0):.2f}"
            for row in rows[:8]
        ]
        reasons: list[str] = [
            f"aggregated_effectiveness_rows:{len(rows)}",
            f"status_mix:{','.join(horizon_status)}",
        ]
        anchor_reasons = self._parse_json(
            anchor.get("reasons_json"),
            [],
            field=f"wiki_page_effectiveness.reasons_json:{anchor.get('page_id')}",
        )
        reasons.extend(str(item)[:180] for item in list(anchor_reasons)[:4])
        return {
            **anchor,
            "venue": "",
            "horizon": "",
            "sample_count": sample_count,
            "win_rate": weighted_average("win_rate"),
            "expectancy": weighted_average("expectancy"),
            "avg_return_pct": weighted_average("avg_return_pct"),
            "median_mae_pct": min(
                float(row.get("median_mae_pct") or 0.0) for row in rows
            ),
            "drawdown_pressure": max(
                float(row.get("drawdown_pressure") or 0.0) for row in rows
            ),
            "helpful_score": helpful_score,
            "confidence": max(float(row.get("confidence") or 0.0) for row in rows),
            "status": status,
            "reasons_json": _json_dumps(reasons),
            "updated_at": max(str(row.get("updated_at") or "") for row in rows),
            **({"metric_presence": metric_presence} if tracked_presence_rows else {}),
        }

    def repair_once(self, *, scope: str | None = None) -> dict[str, Any]:
        from tradecraft.services.jue_wiki_repair import JueWikiRepairService

        return JueWikiRepairService(self).run_once(scope=scope)

    def page_sources(self, page_id: str) -> dict[str, Any]:
        self.initialize()
        page_found = False
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM wiki_source_refs
                WHERE page_id = ?
                ORDER BY source_type ASC, source_id ASC
                """,
                (page_id,),
            ).fetchall()
            source_refs = [
                {
                    "source_type": str(row["source_type"]),
                    "source_id": str(row["source_id"]),
                    "source_path": str(row["source_path"] or ""),
                    "source_scope": str(row["source_scope"] or ""),
                    "observed_at": str(row["observed_at"] or ""),
                    "content_hash": str(row["content_hash"] or ""),
                    "created_at": str(row["created_at"] or ""),
                }
                for row in rows
            ]
            page = conn.execute(
                """
                SELECT source_refs_json
                FROM wiki_pages
                WHERE page_id = ? AND status = 'active'
                """,
                (page_id,),
            ).fetchone()
            parsed_refs: list[dict[str, Any]] = []
            if page is not None:
                page_found = True
                parsed = self._parse_json(
                    page["source_refs_json"],
                    [],
                    field=f"wiki_pages.source_refs_json:{page_id}",
                )
                if isinstance(parsed, list):
                    parsed_refs = self._flatten_source_refs(parsed)
            if source_refs and parsed_refs:
                extra_by_key = {
                    (
                        str(ref.get("source_type") or ""),
                        self._source_ref_index_id(ref),
                    ): ref
                    for ref in parsed_refs
                }
                for ref in source_refs:
                    extra = extra_by_key.get(
                        (
                            str(ref.get("source_type") or ""),
                            str(ref.get("source_id") or ""),
                        ),
                        {},
                    )
                    for key, value in extra.items():
                        ref.setdefault(key, value)
            elif parsed_refs:
                source_refs = parsed_refs
        status = "ok" if page_found else "not_found"
        return {"status": status, "page_id": page_id, "source_refs": source_refs}

    def _scope_filter_prefix(self, scope: str | None) -> str:
        clean_scope = str(scope or "").strip().lower()
        if clean_scope in {"", "all"}:
            return ""
        return _normalize_scope(clean_scope)

    def _lint_page(self, row: sqlite3.Row) -> list[dict[str, Any]]:
        page_id = str(row["page_id"])
        page_scope = str(row["scope"])
        content = self._lint_page_content(row)
        source_refs = self._parse_json(
            row["source_refs_json"],
            [],
            field=f"wiki_pages.source_refs_json:{page_id}",
        )
        flattened_source_refs = self._flatten_source_refs(source_refs)
        findings: list[dict[str, Any]] = []
        if not source_refs:
            findings.append(
                self._lint_finding(
                    page_id=page_id,
                    severity="warn",
                    finding_type="missing_sources",
                    message="Page has no source refs.",
                    evidence={"source_refs_json": row["source_refs_json"]},
                )
            )
        identity_gaps: list[dict[str, Any]] = []
        for index, ref in enumerate(flattened_source_refs):
            source_type = str(ref.get("source_type") or "").strip()
            source_id = str(ref.get("source_id") or "").strip()
            missing = [
                key
                for key, value in (
                    ("source_type", source_type),
                    ("source_id", source_id),
                )
                if not value
            ]
            if not missing:
                continue
            identity_gaps.append(
                {
                    "index": index,
                    "source_type": source_type,
                    "source_id": self._source_ref_index_id(ref),
                    "missing": missing,
                }
            )
        if identity_gaps:
            findings.append(
                self._lint_finding(
                    page_id=page_id,
                    severity="warn",
                    finding_type="source_ref_identity_gap",
                    message="Page has source refs with missing source_type or source_id.",
                    evidence={
                        "gap_count": len(identity_gaps),
                        "examples": identity_gaps[:8],
                    },
                )
            )
        if int(row["char_count"] or 0) > self.config.page_max_chars or (
            content and len(content) > self.config.page_max_chars
        ):
            findings.append(
                self._lint_finding(
                    page_id=page_id,
                    severity="warn",
                    finding_type="oversized_page",
                    message="Page exceeds configured page budget.",
                    evidence={
                        "char_count": int(row["char_count"] or 0),
                        "file_char_count": len(content),
                        "page_max_chars": self.config.page_max_chars,
                    },
                )
            )
        if page_scope == "kis" and re.search(r"\b[A-Z]{2,20}USDT\b", content):
            findings.append(
                self._lint_finding(
                    page_id=page_id,
                    severity="warn",
                    finding_type="scope_leakage",
                    message="KIS page contains crypto USDT symbol.",
                )
            )
        if page_scope == "binance" and self._contains_krx_symbol_leakage(content):
            findings.append(
                self._lint_finding(
                    page_id=page_id,
                    severity="warn",
                    finding_type="scope_leakage",
                    message="Binance page contains Korean equity code.",
                )
            )
        if self._is_stale_page(row):
            findings.append(
                self._lint_finding(
                    page_id=page_id,
                    severity="warn",
                    finding_type="stale_page",
                    message="Page freshness is stale or updated_at is old.",
                    evidence={
                        "freshness": row["freshness"],
                        "updated_at": row["updated_at"],
                    },
                )
            )
        return findings

    def _contains_krx_symbol_leakage(self, content: str) -> bool:
        if not content:
            return False
        for match in re.finditer(r"(?<![\w.:-])\d{6}(?![\w.:-])", content):
            if self._is_operational_metric_number_context(content, match):
                continue
            return True
        return False

    def _is_operational_metric_number_context(
        self,
        content: str,
        match: re.Match[str],
    ) -> bool:
        start = max(match.start() - 48, 0)
        end = min(match.end() + 48, len(content))
        context = content[start:end].lower()
        metric_markers = (
            "total_chars=",
            "max_chars=",
            "char_count=",
            "file_char_count=",
            "page_max_chars",
            "token_estimate=",
            "prompt_budget_exceeded",
        )
        return any(marker in context for marker in metric_markers)

    def _lint_page_content(self, row: sqlite3.Row) -> str:
        path_value = str(row["path"] or "").strip()
        path = (
            Path(path_value)
            if path_value
            else self.page_path(page_id=row["page_id"])
        )
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _is_stale_page(self, row: sqlite3.Row) -> bool:
        if _wiki_freshness_signal(row["freshness"]) == "stale":
            return True
        updated_at = self._parse_datetime(str(row["updated_at"] or ""))
        if updated_at is None:
            return False
        return datetime.now(timezone.utc) - updated_at > timedelta(days=14)

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        if not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _lint_finding(
        self,
        *,
        page_id: str,
        severity: str,
        finding_type: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finding_id = _hash_text(f"{page_id}:{finding_type}:{message}")[:24]
        return {
            "finding_id": finding_id,
            "page_id": page_id,
            "severity": severity,
            "finding_type": finding_type,
            "message": message,
            "evidence": evidence or {},
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _rebuild_kis_symbols(self) -> int:
        blocks = self._read_kis_symbol_blocks()
        reflections = self._read_reflections_by_symbol(scope="kis")
        manager_observations = self._read_kis_manager_observations_by_symbol()
        self._record_manager_observation_repair_actions(
            scope="kis",
            observations_by_symbol=manager_observations,
        )
        self._resolve_manager_observation_repair_actions(
            scope="kis",
            observations_by_symbol=manager_observations,
        )
        discovery_notes = self._read_daily_discovery_by_symbol()
        report_notes = self._read_naver_reports_by_symbol()
        fundamentals_notes = self._read_symbol_fundamentals_by_symbol()
        etf_notes = self._read_etf_research_by_symbol()
        strategy_signals = self._read_strategy_insights_by_symbol()
        updated = 0
        for symbol in sorted(
            set(blocks)
            | set(reflections)
            | set(manager_observations)
            | set(discovery_notes)
            | set(report_notes)
            | set(fundamentals_notes)
            | set(etf_notes)
            | set(strategy_signals)
        ):
            symbol_blocks = blocks.get(symbol, [])[:12]
            symbol_reflections = reflections.get(symbol, [])[:12]
            symbol_observations = manager_observations.get(symbol, [])[:12]
            symbol_discoveries = discovery_notes.get(symbol, [])[:12]
            symbol_reports = report_notes.get(symbol, [])[:8]
            symbol_fundamentals = fundamentals_notes.get(symbol, [])[:3]
            symbol_etfs = etf_notes.get(symbol, [])[:8]
            symbol_signals = strategy_signals.get(symbol, [])[:10]
            if (
                not symbol_blocks
                and not symbol_reflections
                and not symbol_observations
                and not symbol_discoveries
                and not symbol_reports
                and not symbol_fundamentals
                and not symbol_etfs
                and not symbol_signals
            ):
                continue
            name_candidates = [
                symbol_blocks[0].get("name") if symbol_blocks else "",
                symbol_reflections[0].get("name") if symbol_reflections else "",
                symbol_observations[0].get("name") if symbol_observations else "",
                symbol_discoveries[0].get("name") if symbol_discoveries else "",
                symbol_reports[0].get("name") if symbol_reports else "",
                symbol_fundamentals[0].get("name") if symbol_fundamentals else "",
                symbol_etfs[0].get("name") if symbol_etfs else "",
                symbol_signals[0].get("name") if symbol_signals else "",
            ]
            name = next(
                (str(value).strip() for value in name_candidates if str(value).strip()),
                symbol,
            )
            source_refs = [
                {
                    "source_type": "kis_blocks",
                    "source_id": str(row.get("block_id") or ""),
                    "source_scope": "kis",
                    "observed_at": str(
                        row.get("closed_at") or row.get("created_at") or ""
                    ),
                }
                for row in symbol_blocks
                if str(row.get("block_id") or "").strip()
            ] + [
                {
                    "source_type": "investment_memory",
                    "source_id": str(row.get("block_id") or ""),
                    "source_scope": "kis",
                    "observed_at": str(row.get("created_at") or ""),
                }
                for row in symbol_reflections
                if str(row.get("block_id") or "").strip()
            ] + [
                {
                    "source_type": "kis_manager_runs",
                    "source_id": str(row.get("manager_run_id") or ""),
                    "source_scope": "kis",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_observations
                if str(row.get("manager_run_id") or "").strip()
            ] + [
                {
                    "source_type": "daily_discovery",
                    "source_id": str(row.get("trading_day") or ""),
                    "source_scope": "kis",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_discoveries
                if str(row.get("trading_day") or "").strip()
            ] + [
                {
                    "source_type": "symbol_fundamentals",
                    "source_id": str(
                        row.get("source_id") or row.get("symbol") or ""
                    ),
                    "source_path": str(row.get("source_url") or ""),
                    "source_scope": "research",
                    "observed_at": str(
                        row.get("crawled_at")
                        or row.get("scored_at")
                        or row.get("as_of")
                        or ""
                    ),
                    "quality_status": _normalize_quality_status(
                        row.get("quality_status")
                    ),
                    "quality_warnings": list(row.get("quality_warnings") or []),
                }
                for row in symbol_fundamentals
                if str(row.get("source_id") or row.get("symbol") or "").strip()
            ] + [
                {
                    "source_type": "etf_research",
                    "source_id": str(row.get("source_id") or row.get("symbol") or ""),
                    "source_scope": "research",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_etfs
                if str(row.get("source_id") or row.get("symbol") or "").strip()
            ] + [
                {
                    "source_type": "strategy_insight",
                    "source_id": str(row.get("signal_id") or ""),
                    "source_scope": "research",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_signals
                if str(row.get("signal_id") or "").strip()
            ]
            content_sections = self._build_kis_symbol_sections(
                symbol=symbol,
                name=name,
                blocks=symbol_blocks,
                reflections=symbol_reflections,
                manager_observations=symbol_observations,
                discovery_notes=symbol_discoveries,
                fundamentals=symbol_fundamentals,
                etf_notes=symbol_etfs,
                strategy_signals=symbol_signals,
            )
            rag_lines, rag_refs = self._rag_research_lines(symbol=symbol)
            if rag_lines:
                content_sections["Evidence Links"] = "\n".join(
                    [
                        content_sections.get("Evidence Links", ""),
                        "### Research",
                        *rag_lines,
                    ]
                ).strip()
            source_refs.extend(rag_refs)
            report_lines, report_refs = self._naver_report_lines_and_refs(
                reports=symbol_reports
            )
            if report_lines:
                content_sections["Evidence Links"] = "\n".join(
                    [
                        content_sections.get("Evidence Links", ""),
                        "### Naver Reports",
                        *report_lines,
                    ]
                ).strip()
            source_refs.extend(report_refs)
            base_confidence = (
                0.68
                if symbol_reflections
                else 0.56
                if (
                    symbol_observations
                    or symbol_discoveries
                    or symbol_reports
                    or symbol_etfs
                    or symbol_signals
                )
                else 0.48
            )
            if symbol_fundamentals:
                fundamentals_confidence = max(
                    float(row.get("quality_confidence") or 0.0)
                    for row in symbol_fundamentals
                )
                base_confidence = max(base_confidence, min(fundamentals_confidence, 0.62))
                if all(
                    _normalize_quality_status(row.get("quality_status")) == "weak"
                    for row in symbol_fundamentals
                ):
                    base_confidence = min(base_confidence, 0.5)
            self.write_page(
                scope="kis",
                page_type="symbol",
                key=symbol,
                title=name,
                symbols=[symbol],
                content_sections=content_sections,
                source_refs=source_refs,
                confidence=base_confidence,
                freshness="fresh",
            )
            updated += 1
        return updated

    def _read_naver_reports_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.naver_reports_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "reports"):
                    raise JueWikiSourceReadError(
                        f"naver reports DB in {source_path} is missing reports table"
                    )
                has_links = self._table_exists(conn, "report_symbol_links")
                has_facts = self._table_exists(conn, "report_facts")
                fact_join = (
                    """
                    LEFT JOIN report_facts AS f
                      ON f.report_id = r.report_id
                    """
                    if has_facts
                    else ""
                )
                if has_links:
                    rows = conn.execute(
                        f"""
                        SELECT
                            r.report_id,
                            COALESCE(NULLIF(l.symbol, ''), r.symbol, '') AS symbol,
                            COALESCE(NULLIF(l.name, ''), r.company_name, r.symbol, '') AS name,
                            r.title,
                            r.company_name,
                            r.broker,
                            r.published_at,
                            r.detail_url,
                            r.pdf_url,
                            l.link_type,
                            l.confidence AS link_confidence,
                            {self._naver_fact_select_sql(has_facts)}
                        FROM reports AS r
                        JOIN report_symbol_links AS l
                          ON l.report_id = r.report_id
                        {fact_join}
                        WHERE LENGTH(COALESCE(NULLIF(l.symbol, ''), r.symbol, '')) = 6
                          AND COALESCE(NULLIF(l.symbol, ''), r.symbol, '') GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                        ORDER BY
                            COALESCE(NULLIF(r.published_at, ''), '') DESC,
                            CASE WHEN l.link_type = 'primary' THEN 1 ELSE 0 END DESC,
                            COALESCE(l.confidence, 0) DESC,
                            r.report_id DESC
                        LIMIT 320
                        """
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT
                            r.report_id,
                            COALESCE(r.symbol, '') AS symbol,
                            COALESCE(r.company_name, r.symbol, '') AS name,
                            r.title,
                            r.company_name,
                            r.broker,
                            r.published_at,
                            r.detail_url,
                            r.pdf_url,
                            'primary' AS link_type,
                            1.0 AS link_confidence,
                            {self._naver_fact_select_sql(has_facts)}
                        FROM reports AS r
                        {fact_join}
                        WHERE LENGTH(COALESCE(r.symbol, '')) = 6
                          AND COALESCE(r.symbol, '') GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                        ORDER BY
                            COALESCE(NULLIF(r.published_at, ''), '') DESC,
                            r.report_id DESC
                        LIMIT 320
                        """
                    ).fetchall()
        except JueWikiSourceReadError:
            raise
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read naver reports DB in {source_path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            for key in (
                "summary_bullets_json",
                "investment_thesis_json",
                "risks_json",
                "catalysts_json",
            ):
                payload[key.removesuffix("_json")] = self._safe_json_list(
                    payload.get(key),
                    field=f"naver_reports.{key}:{payload.get('report_id')}",
                )
            payload["symbol"] = symbol
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _read_symbol_fundamentals_by_symbol(
        self,
    ) -> dict[str, list[dict[str, Any]]]:
        path = self.config.symbol_fundamentals_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "valuation_snapshots"):
                    raise JueWikiSourceReadError(
                        f"symbol fundamentals DB in {source_path} is missing "
                        "valuation_snapshots table"
                    )
                valuation_columns = self._table_columns(conn, "valuation_snapshots")
                score_columns = (
                    self._table_columns(conn, "valuation_scores")
                    if self._table_exists(conn, "valuation_scores")
                    else set()
                )
                has_scores = "symbol" in score_columns
                has_financials = self._table_exists(conn, "financial_snapshots")
                financial_columns = (
                    self._table_columns(conn, "financial_snapshots")
                    if has_financials
                    else set()
                )

                def v_expr(column: str, output_name: str, fallback: str = "NULL") -> str:
                    if column in valuation_columns:
                        return f"v.{column} AS {output_name}"
                    return f"{fallback} AS {output_name}"

                def s_expr(column: str, output_name: str, fallback: str = "NULL") -> str:
                    if column in score_columns:
                        return f"s.{column} AS {output_name}"
                    return f"{fallback} AS {output_name}"

                def f_expr(column: str, output_name: str, fallback: str = "NULL") -> str:
                    if column in financial_columns:
                        return f"{column} AS {output_name}"
                    return f"{fallback} AS {output_name}"

                score_select = (
                    ",\n                        ".join(
                        [
                            s_expr("undervalued_score", "undervalued_score"),
                            s_expr("overvalued_risk", "overvalued_risk"),
                            s_expr("quality_score", "quality_score"),
                            s_expr("growth_score", "growth_score"),
                            s_expr(
                                "relative_per_discount_pct",
                                "relative_per_discount_pct",
                            ),
                            s_expr("pbr_roe_fit", "pbr_roe_fit"),
                            s_expr("label", "valuation_label", "'unknown'"),
                            s_expr("reasons_json", "score_reasons_json", "'[]'"),
                            s_expr("risks_json", "score_risks_json", "'[]'"),
                            s_expr("scored_at", "scored_at", "''"),
                        ]
                    )
                    if has_scores
                    else """
                    NULL AS undervalued_score,
                    NULL AS overvalued_risk,
                    NULL AS quality_score,
                    NULL AS growth_score,
                    NULL AS relative_per_discount_pct,
                    NULL AS pbr_roe_fit,
                    'unknown' AS valuation_label,
                    '[]' AS score_reasons_json,
                    '[]' AS score_risks_json,
                    '' AS scored_at
                    """
                )
                score_join = (
                    "LEFT JOIN valuation_scores AS s ON s.symbol = v.symbol"
                    if has_scores
                    else ""
                )
                valuation_order = (
                    "COALESCE(NULLIF(v.crawled_at, ''), v.as_of, '')"
                    if {"crawled_at", "as_of"} <= valuation_columns
                    else "COALESCE(NULLIF(v.crawled_at, ''), '')"
                    if "crawled_at" in valuation_columns
                    else "COALESCE(NULLIF(v.as_of, ''), '')"
                    if "as_of" in valuation_columns
                    else "v.rowid"
                )
                valuation_tie_breaker = (
                    "v.snapshot_id DESC" if "snapshot_id" in valuation_columns else "v.rowid DESC"
                )
                rows = conn.execute(
                    f"""
                    SELECT
                        {v_expr("symbol", "symbol", "''")},
                        {v_expr("name", "name", "''")},
                        {v_expr("price", "price")},
                        {v_expr("market_cap_krw", "market_cap_krw")},
                        {v_expr("per", "per")},
                        {v_expr("eps", "eps")},
                        {v_expr("pbr", "pbr")},
                        {v_expr("bps", "bps")},
                        {v_expr("dividend_yield_pct", "dividend_yield_pct")},
                        {v_expr("industry_per", "industry_per")},
                        {v_expr("industry_name", "industry_name", "''")},
                        {v_expr("as_of", "as_of", "''")},
                        {v_expr("source_url", "source_url", "''")},
                        {v_expr("crawled_at", "crawled_at", "''")},
                        {v_expr("status", "status", "'ok'")},
                        {v_expr("error_message", "error_message", "''")},
                        {score_select}
                    FROM valuation_snapshots AS v
                    {score_join}
                    WHERE LENGTH(COALESCE(v.symbol, '')) = 6
                      AND COALESCE(v.symbol, '') GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                    ORDER BY
                        {valuation_order} DESC,
                        {valuation_tie_breaker}
                    LIMIT 900
                    """
                ).fetchall()
                financial_rows = []
                if has_financials:
                    financial_rows = conn.execute(
                        f"""
                        SELECT
                            {f_expr("symbol", "symbol", "''")},
                            {f_expr("period_type", "period_type", "''")},
                            {f_expr("period", "period", "''")},
                            {f_expr("revenue", "revenue")},
                            {f_expr("operating_profit", "operating_profit")},
                            {f_expr("net_income", "net_income")},
                            {f_expr("roe", "roe")},
                            {f_expr("debt_ratio", "debt_ratio")},
                            {f_expr("operating_margin", "operating_margin")},
                            {f_expr("crawled_at", "crawled_at", "''")}
                        FROM financial_snapshots
                        WHERE LENGTH(COALESCE(symbol, '')) = 6
                          AND COALESCE(symbol, '') GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                        ORDER BY
                            {self._order_expr(table_columns=financial_columns, preferred=["crawled_at", "period"])} DESC,
                            {"financial_id DESC" if "financial_id" in financial_columns else "rowid DESC"}
                        LIMIT 1200
                        """
                    ).fetchall()
        except JueWikiSourceReadError:
            raise
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read symbol fundamentals DB in {source_path}: {exc}"
            ) from exc

        financials_by_symbol: dict[str, list[dict[str, Any]]] = {}
        financial_warnings_by_symbol: dict[str, list[str]] = {}
        for row in financial_rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            row_warnings = self._financial_snapshot_quality_warnings(payload)
            if row_warnings:
                bucket = financial_warnings_by_symbol.setdefault(symbol, [])
                for warning in row_warnings:
                    if warning not in bucket:
                        bucket.append(warning)
                continue
            payload["symbol"] = symbol
            if len(financials_by_symbol.setdefault(symbol, [])) < 3:
                financials_by_symbol[symbol].append(payload)

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            if len(grouped.get(symbol, [])) >= 2:
                continue
            as_of = str(payload.get("as_of") or "").strip()
            observed_at = str(
                payload.get("crawled_at")
                or payload.get("scored_at")
                or as_of
                or ""
            )
            payload["symbol"] = symbol
            payload["source_id"] = f"{symbol}:{as_of or observed_at or 'latest'}"
            payload["score_reasons"] = self._safe_json_list(
                payload.get("score_reasons_json"),
                field=f"valuation_scores.reasons_json:{symbol}",
            )
            payload["score_risks"] = self._safe_json_list(
                payload.get("score_risks_json"),
                field=f"valuation_scores.risks_json:{symbol}",
            )
            payload["financials"] = financials_by_symbol.get(symbol, [])
            payload["financial_warnings"] = financial_warnings_by_symbol.get(symbol, [])
            payload["quality"] = self._symbol_fundamentals_quality(payload)
            payload["quality_status"] = payload["quality"]["status"]
            payload["quality_warnings"] = payload["quality"]["warnings"]
            payload["quality_confidence"] = payload["quality"]["confidence"]
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _financial_snapshot_quality_warnings(self, row: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        period = str(row.get("period") or "")
        if re.search(
            r"\b(?:AAA|AA[+-]?|A1|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?)\s*\[\d{6,8}\]",
            period,
        ):
            warnings.append("financial_rows_rejected_credit_rating")
        metric_count = sum(
            1
            for key in (
                "revenue",
                "operating_profit",
                "net_income",
                "roe",
                "debt_ratio",
                "operating_margin",
            )
            if self._safe_float_for_quality(row.get(key)) is not None
        )
        if metric_count == 0:
            warnings.append("financial_rows_rejected_empty")
        return warnings

    def _symbol_fundamentals_quality(self, row: dict[str, Any]) -> dict[str, Any]:
        symbol = _normalize_symbol(str(row.get("symbol") or ""))
        name = str(row.get("name") or "").strip()
        warnings: list[str] = []
        status_value = str(row.get("status") or "ok").strip().lower()
        if status_value and status_value != "ok":
            warnings.append("valuation_status_not_ok")
        if not name or name == symbol:
            warnings.append("identity_name_missing")
        price = self._safe_float_for_quality(row.get("price"))
        per = self._safe_float_for_quality(row.get("per"))
        eps = self._safe_float_for_quality(row.get("eps"))
        pbr = self._safe_float_for_quality(row.get("pbr"))
        bps = self._safe_float_for_quality(row.get("bps"))
        market_cap = self._safe_float_for_quality(row.get("market_cap_krw"))
        industry_per = self._safe_float_for_quality(row.get("industry_per"))
        if price is None or price <= 0:
            warnings.append("price_missing")
        metric_values = [per, eps, pbr, bps, market_cap, industry_per]
        if sum(1 for value in metric_values if value not in (None, 0)) < 3:
            warnings.append("valuation_metrics_sparse")
        if price and eps and eps > 0 and per and per > 0:
            implied_per = price / eps
            if self._relative_gap(implied_per, per) > 0.45:
                warnings.append("per_price_eps_mismatch")
        if price and bps and bps > 0 and pbr and pbr > 0:
            implied_pbr = price / bps
            if self._relative_gap(implied_pbr, pbr) > 0.45:
                warnings.append("pbr_price_bps_mismatch")
        if market_cap and price and price > 0:
            shares_estimate = market_cap / price
            if shares_estimate < 500_000 or shares_estimate > 25_000_000_000:
                warnings.append("market_cap_price_share_count_outlier")
        for warning in list(row.get("financial_warnings") or []):
            if str(warning).strip():
                warnings.append(str(warning).strip())
        financials = list(row.get("financials") or [])
        if not financials:
            warnings.append("financials_missing")
        else:
            usable_financial_rows = 0
            for financial in financials:
                period = str(financial.get("period") or "")
                if re.search(
                    r"\b(?:AAA|AA[+-]?|A1|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?)\s*\[\d{6,8}\]",
                    period,
                ):
                    warnings.append("financial_period_credit_rating_noise")
                metric_count = sum(
                    1
                    for key in (
                        "revenue",
                        "operating_profit",
                        "net_income",
                        "roe",
                        "debt_ratio",
                        "operating_margin",
                    )
                    if self._safe_float_for_quality(financial.get(key)) is not None
                )
                if metric_count >= 2:
                    usable_financial_rows += 1
            if usable_financial_rows == 0:
                warnings.append("financial_metrics_sparse")
        observed_at = str(row.get("crawled_at") or row.get("as_of") or "")
        observed_dt = self._parse_datetime(observed_at)
        if observed_dt is not None:
            age_days = (datetime.now(timezone.utc) - observed_dt).days
            if age_days > 30:
                warnings.append("valuation_stale_gt_30d")
            elif age_days > 7:
                warnings.append("valuation_aging_gt_7d")
        warnings = list(dict.fromkeys(warnings))
        severe = {
            "valuation_status_not_ok",
            "identity_name_missing",
            "price_missing",
            "valuation_metrics_sparse",
            "market_cap_price_share_count_outlier",
        }
        severe_count = sum(1 for item in warnings if item in severe)
        if severe_count:
            quality_status = "weak" if severe_count >= 2 else "partial"
        elif warnings:
            quality_status = "partial"
        else:
            quality_status = "strong"
        confidence = {
            "strong": 0.68,
            "partial": 0.56,
            "weak": 0.42,
        }[quality_status]
        return {
            "status": quality_status,
            "confidence": confidence,
            "warnings": warnings,
        }

    @staticmethod
    def _safe_float_for_quality(value: Any) -> float | None:
        if value in (None, "", [], {}):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _relative_gap(left: float, right: float) -> float:
        base = max(abs(left), abs(right), 1.0)
        return abs(left - right) / base

    @staticmethod
    def _naver_fact_select_sql(has_facts: bool) -> str:
        if not has_facts:
            return (
                "'' AS rating, '' AS target_price_value, "
                "'' AS target_price_currency, '[]' AS summary_bullets_json, "
                "'[]' AS investment_thesis_json, '[]' AS risks_json, "
                "'[]' AS catalysts_json"
            )
        return (
            "f.rating, f.target_price_value, f.target_price_currency, "
            "f.summary_bullets_json, f.investment_thesis_json, "
            "f.risks_json, f.catalysts_json"
        )

    def _naver_report_lines_and_refs(
        self,
        *,
        reports: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        lines: list[str] = []
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in reports[:6]:
            report_id = str(row.get("report_id") or "").strip()
            if not report_id or report_id in seen:
                continue
            seen.add(report_id)
            title = str(row.get("title") or "").strip()[:180] or "untitled"
            published_at = str(row.get("published_at") or "").strip()
            broker = str(row.get("broker") or "").strip()
            rating = str(row.get("rating") or "").strip()
            target = str(row.get("target_price_value") or "").strip()
            currency = str(row.get("target_price_currency") or "").strip()
            summary = "; ".join(
                str(item).strip()
                for item in list(row.get("summary_bullets") or [])[:2]
                if str(item).strip()
            )
            thesis = "; ".join(
                str(item).strip()
                for item in list(row.get("investment_thesis") or [])[:2]
                if str(item).strip()
            )
            risks = "; ".join(
                str(item).strip()
                for item in list(row.get("risks") or [])[:2]
                if str(item).strip()
            )
            catalysts = "; ".join(
                str(item).strip()
                for item in list(row.get("catalysts") or [])[:2]
                if str(item).strip()
            )
            lines.append(
                "- report_id={report_id}, date={date}, title={title}, "
                "broker={broker}, rating={rating}, target={target}{currency}; "
                "summary={summary}; thesis={thesis}; risks={risks}; catalysts={catalysts}".format(
                    report_id=report_id,
                    date=published_at or "-",
                    title=title,
                    broker=broker or "-",
                    rating=rating or "-",
                    target=target or "-",
                    currency=f" {currency}" if currency else "",
                    summary=summary[:260] or "-",
                    thesis=thesis[:260] or "-",
                    risks=risks[:220] or "-",
                    catalysts=catalysts[:220] or "-",
                )
            )
            lines.append(f"- naver_reports:{report_id}")
            refs.append(
                {
                    "source_type": "naver_reports",
                    "source_id": report_id,
                    "source_path": str(
                        row.get("detail_url") or row.get("pdf_url") or ""
                    ),
                    "source_scope": "research",
                    "observed_at": published_at,
                }
            )
        return lines, refs

    def _read_etf_research_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.etf_research_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "etf_scores"):
                    return {}
                has_snapshots = self._table_exists(conn, "etf_market_snapshots")
                has_universe = self._table_exists(conn, "etf_universe")
                snapshot_join = (
                    """
                    LEFT JOIN etf_market_snapshots AS m
                      ON m.id = s.id
                    """
                    if has_snapshots
                    else ""
                )
                universe_join = (
                    """
                    LEFT JOIN etf_universe AS u
                      ON u.symbol = s.symbol
                    """
                    if has_universe
                    else ""
                )
                rows = conn.execute(
                    f"""
                    SELECT
                        s.id AS source_id,
                        s.symbol,
                        {("COALESCE(NULLIF(u.name, ''), NULLIF(m.name, ''), s.symbol)" if has_snapshots and has_universe else "s.symbol")} AS name,
                        {("u.category" if has_universe else "''")} AS category,
                        {("m.price" if has_snapshots else "''")} AS price,
                        {("m.change_pct" if has_snapshots else "''")} AS change_pct,
                        {("m.turnover_krw" if has_snapshots else "''")} AS turnover_krw,
                        {("m.volume" if has_snapshots else "''")} AS volume,
                        s.label,
                        s.liquidity_score,
                        s.momentum_score,
                        s.core_fit_score,
                        s.risk_score,
                        s.reasons_json,
                        s.risks_json,
                        s.scored_at AS observed_at
                    FROM etf_scores AS s
                    {snapshot_join}
                    {universe_join}
                    WHERE LENGTH(COALESCE(s.symbol, '')) = 6
                      AND COALESCE(s.symbol, '') GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                    ORDER BY COALESCE(NULLIF(s.scored_at, ''), '') DESC,
                             s.liquidity_score DESC,
                             s.momentum_score DESC
                    LIMIT 640
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read ETF research DB in {source_path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            payload["symbol"] = symbol
            payload["reasons"] = self._safe_json_list(
                payload.get("reasons_json"),
                field=f"etf_scores.reasons_json:{symbol}",
            )
            payload["risks"] = self._safe_json_list(
                payload.get("risks_json"),
                field=f"etf_scores.risks_json:{symbol}",
            )
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _read_strategy_insights_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.strategy_insights_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "strategy_signals"):
                    return {}
                rows = conn.execute(
                    """
                    SELECT signal_id, source_id, symbol, name, signal_type,
                           direction, strength, summary, as_of, tags_json,
                           collected_at, updated_at
                    FROM strategy_signals
                    WHERE LENGTH(COALESCE(symbol, '')) = 6
                      AND COALESCE(symbol, '') GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                    ORDER BY COALESCE(NULLIF(as_of, ''), NULLIF(collected_at, ''), '') DESC,
                             strength DESC
                    LIMIT 720
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read strategy insights DB in {source_path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not re.fullmatch(r"\d{6}", symbol):
                continue
            payload["symbol"] = symbol
            payload["tags"] = self._safe_json_list(
                payload.get("tags_json"),
                field=f"strategy_signals.tags_json:{payload.get('signal_id')}",
            )
            payload["observed_at"] = (
                str(payload.get("as_of") or payload.get("collected_at") or "")
            )
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _rebuild_research_coverage_page(self, *, scope: str) -> int:
        clean_scope = _normalize_scope(scope)
        rows = self._research_coverage_rows(scope=clean_scope)
        if not rows:
            return 0
        if all(str(row.get("status") or "") == "missing_path" for row in rows):
            return 0
        coverage_lines: list[str] = []
        freshness_lines: list[str] = []
        gap_lines: list[str] = []
        action_lines: list[str] = []
        source_refs: list[dict[str, Any]] = []
        ok_count = 0
        total_rows = 0
        latest_values: list[str] = []
        for row in rows:
            source_id = str(row.get("source_id") or "")
            status = str(row.get("status") or "unknown")
            latest_at = str(row.get("latest_at") or "")
            row_count = int(row.get("rows") or 0)
            symbol_count = int(row.get("symbols") or 0)
            total_rows += row_count
            if status == "ok":
                ok_count += 1
            if latest_at:
                latest_values.append(latest_at)
            coverage_lines.append(
                "- {source_id}: status={status}, primary_table={table}, "
                "rows={rows}, symbols={symbols}, latest_at={latest_at}".format(
                    source_id=source_id,
                    status=status,
                    table=row.get("primary_table") or "-",
                    rows=row_count,
                    symbols=symbol_count,
                    latest_at=latest_at or "-",
                )
            )
            table_counts = [
                "{table}:{rows}".format(
                    table=detail.get("table") or "-",
                    rows=detail.get("rows") or 0,
                )
                for detail in list(row.get("tables") or [])[:6]
                if isinstance(detail, dict)
            ]
            freshness_lines.append(
                "- {source_id}: latest_at={latest_at}, table_counts={counts}".format(
                    source_id=source_id,
                    latest_at=latest_at or "-",
                    counts=", ".join(table_counts) if table_counts else "-",
                )
            )
            if status != "ok" or row_count <= 0:
                gap_lines.append(
                    "- {source_id}: status={status}, reason={reason}".format(
                        source_id=source_id,
                        status=status,
                        reason=row.get("reason") or "no usable rows",
                    )
                )
            else:
                action_lines.append(
                    "- {source_id}: use {rows} rows as candidate expansion and "
                    "evidence checks before scaling.".format(
                        source_id=source_id,
                        rows=row_count,
                    )
                )
            source_refs.append(
                {
                    "source_type": "research_coverage",
                    "source_id": source_id,
                    "source_path": str(row.get("path") or ""),
                    "source_scope": clean_scope,
                    "observed_at": latest_at,
                }
            )
        latest_overall = max(latest_values) if latest_values else ""
        durable_facts = "\n".join(
            [
                f"- scope={clean_scope}",
                f"- source_count={len(rows)}",
                f"- ok_source_count={ok_count}",
                f"- total_primary_rows={total_rows}",
                f"- latest_overall_at={latest_overall or '-'}",
            ]
        )
        self.write_page(
            scope=clean_scope,
            page_type="research",
            key="coverage",
            title=f"{clean_scope.upper()} Research Coverage",
            symbols=[],
            content_sections={
                "Current Stance": (
                    f"{clean_scope} research coverage is active across "
                    f"{ok_count}/{len(rows)} sources. Jue should use this page "
                    "to see available, stale, and missing evidence layers before "
                    "designing blocks."
                ),
                "Durable Facts": durable_facts,
                "Evidence Links": "\n".join(
                    f"- research_coverage:{ref['source_id']}" for ref in source_refs
                )
                or "- No linked evidence.",
                "Trading History": (
                    "- Research coverage is pre-trade evidence memory, not a "
                    "standalone trade signal."
                ),
                "Lessons": (
                    "- Dense research coverage should expand candidate breadth, "
                    "not blind confidence.\n"
                    "- Missing or stale research becomes a follow-up check, "
                    "waiting-entry condition, or reduced sizing reason."
                ),
                "Contradictions": (
                    "- If live performance is weak while research coverage is broad, "
                    "the bottleneck is likely evidence-to-price-structure translation."
                ),
                "Open Questions": (
                    "- Which source is finding candidates before price moves?\n"
                    "- Which source is stale enough to downweight?\n"
                    "- Which symbols have research but no executable block design?"
                ),
                "Next Context Pack Summary": (
                    f"{clean_scope} research coverage: ok_sources={ok_count}/"
                    f"{len(rows)}, total_primary_rows={total_rows}, "
                    f"latest={latest_overall or '-'}. Use gaps as repair tasks "
                    "and dense coverage as candidate expansion fuel."
                ),
                "Coverage Matrix": "\n".join(coverage_lines)
                or "- No research source configured.",
                "Freshness": "\n".join(freshness_lines) or "- No freshness data.",
                "Data Gaps": "\n".join(gap_lines) or "- No explicit coverage gap.",
                "Actionability": "\n".join(action_lines)
                or "- No actionable research source yet.",
            },
            source_refs=source_refs,
            confidence=0.8 if ok_count else 0.35,
            freshness="fresh" if ok_count else "stale",
        )
        return 1

    def _rebuild_evidence_quality_page(self, *, scope: str) -> int:
        clean_scope = _normalize_scope(scope)
        rows, total_source_refs, active_page_count = self._evidence_quality_rows(
            scope=clean_scope
        )
        if not rows:
            return 0
        tagged_refs = [
            {
                key: value
                for key, value in {
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "quality_status": row["quality_status"],
                    "quality_warnings": row["quality_warnings"],
                    "evidence_quality": row.get("evidence_quality"),
                }.items()
                if value not in (None, "", [], {})
            }
            for row in rows
        ]
        quality = self.source_refs_quality_summary(tagged_refs)
        status_counts = dict(quality.get("status_counts") or {})
        warning_counts = dict(quality.get("warning_counts") or {})
        type_counts = dict(quality.get("source_type_counts") or {})
        top_warning_lines = [
            f"- {warning}: count={count}"
            for warning, count in sorted(
                warning_counts.items(),
                key=lambda item: (-int(item[1]), item[0]),
            )[:12]
        ]
        type_lines = [
            f"- source_type={source_type}, quality_tagged_refs={count}"
            for source_type, count in sorted(
                type_counts.items(),
                key=lambda item: (-int(item[1]), item[0]),
            )[:12]
        ]
        page_scores: dict[str, dict[str, Any]] = {}

        def safe_count(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        for row in rows:
            page_id = str(row.get("page_id") or "")
            page = page_scores.setdefault(
                page_id,
                {
                    "page_id": page_id,
                    "title": row.get("title") or page_id,
                    "symbols": row.get("symbols") or [],
                    "warning_count": 0,
                    "weak_count": 0,
                    "partial_count": 0,
                    "warnings": {},
                    "source_types": {},
                },
            )
            status = str(row.get("quality_status") or "")
            source_type = str(row.get("source_type") or "unknown")
            evidence_quality = (
                row.get("evidence_quality")
                if isinstance(row.get("evidence_quality"), dict)
                else {}
            )
            row_status_counts = (
                evidence_quality.get("status_counts")
                if isinstance(evidence_quality.get("status_counts"), dict)
                else {}
            )
            canonical_status_counts: dict[str, int] = {}
            for counted_status, count in row_status_counts.items():
                clean_status = _normalize_quality_status(counted_status)
                if not clean_status:
                    continue
                canonical_status_counts[clean_status] = (
                    canonical_status_counts.get(clean_status, 0) + safe_count(count)
                )
            row_warning_counts = (
                evidence_quality.get("warning_counts")
                if isinstance(evidence_quality.get("warning_counts"), dict)
                else {}
            )
            row_type_counts = (
                evidence_quality.get("source_type_counts")
                if isinstance(evidence_quality.get("source_type_counts"), dict)
                else {}
            )
            if row_status_counts:
                page["weak_count"] += safe_count(canonical_status_counts.get("weak"))
                page["partial_count"] += safe_count(
                    canonical_status_counts.get("partial")
                )
            elif status == "weak":
                page["weak_count"] += 1
            elif status == "partial":
                page["partial_count"] += 1
            if row_type_counts:
                for counted_type, count in row_type_counts.items():
                    clean_type = str(counted_type or "unknown")
                    page["source_types"][clean_type] = (
                        page["source_types"].get(clean_type, 0) + safe_count(count)
                    )
            else:
                page["source_types"][source_type] = (
                    page["source_types"].get(source_type, 0) + 1
                )
            if row_warning_counts:
                for warning, count in row_warning_counts.items():
                    clean_warning = str(warning)
                    clean_count = safe_count(count)
                    if clean_warning and clean_count > 0:
                        page["warning_count"] += clean_count
                        page["warnings"][clean_warning] = (
                            page["warnings"].get(clean_warning, 0) + clean_count
                        )
            else:
                for warning in list(row.get("quality_warnings") or []):
                    warning = str(warning)
                    page["warning_count"] += 1
                    page["warnings"][warning] = page["warnings"].get(warning, 0) + 1
        affected_pages = sorted(
            page_scores.values(),
            key=lambda row: (
                -int(row.get("weak_count") or 0),
                -int(row.get("warning_count") or 0),
                str(row.get("page_id") or ""),
            ),
        )
        affected_lines = []
        for row in affected_pages[:16]:
            warnings = ", ".join(
                f"{warning}:{count}"
                for warning, count in sorted(
                    dict(row.get("warnings") or {}).items(),
                    key=lambda item: (-int(item[1]), item[0]),
                )[:5]
            )
            source_types = ", ".join(
                f"{source_type}:{count}"
                for source_type, count in sorted(
                    dict(row.get("source_types") or {}).items(),
                    key=lambda item: (-int(item[1]), item[0]),
                )[:4]
            )
            symbols = ",".join(str(symbol) for symbol in list(row.get("symbols") or [])[:6])
            affected_lines.append(
                "- page={page_id}, title={title}, symbols={symbols}, "
                "weak={weak}, partial={partial}, warning_count={warning_count}, "
                "source_types={source_types}, warnings={warnings}".format(
                    page_id=row.get("page_id") or "-",
                    title=row.get("title") or "-",
                    symbols=symbols or "-",
                    weak=row.get("weak_count") or 0,
                    partial=row.get("partial_count") or 0,
                    warning_count=row.get("warning_count") or 0,
                    source_types=source_types or "-",
                    warnings=warnings or "-",
                )
            )
        source_refs = [
            {
                "source_type": "wiki_evidence_quality",
                "source_id": f"{row.get('page_id')}:{row.get('source_type')}:{row.get('source_id')}",
                "source_scope": clean_scope,
                "source_path": str(row.get("page_id") or ""),
                "observed_at": str(row.get("observed_at") or ""),
            }
            for row in rows[:64]
        ]
        summary_line = str(quality.get("summary_line") or "no quality-tagged source refs")
        self.write_page(
            scope=clean_scope,
            page_type="research",
            key="evidence_quality",
            title=f"{clean_scope.upper()} Evidence Quality",
            symbols=[],
            content_sections={
                "Current Stance": (
                    f"{clean_scope} evidence quality guardrail: {summary_line}. "
                    "Jue should discount partial or weak source layers, keep them "
                    "as repair tasks, and prefer executable blocks backed by clean "
                    "or cross-checked evidence."
                ),
                "Durable Facts": "\n".join(
                    [
                        f"- scope={clean_scope}",
                        f"- active_page_count={active_page_count}",
                        f"- total_source_ref_count={total_source_refs}",
                        f"- quality_tagged_source_count={int(quality.get('source_count') or len(rows))}",
                        f"- strong_source_count={int(status_counts.get('strong') or 0)}",
                        f"- partial_source_count={int(status_counts.get('partial') or 0)}",
                        f"- weak_source_count={int(status_counts.get('weak') or 0)}",
                    ]
                ),
                "Evidence Links": "\n".join(
                    f"- wiki_evidence_quality:{ref['source_id']}"
                    for ref in source_refs[:32]
                )
                or "- No quality-tagged source refs yet.",
                "Trading History": (
                    "- Evidence quality is pre-trade memory. It modifies trust in "
                    "research, valuation, and execution notes before block design."
                ),
                "Lessons": (
                    "- Strong evidence can support normal sizing only after live "
                    "price structure is executable.\n"
                    "- Partial evidence needs cross-checks, smaller probes, or "
                    "waiting-entry blocks.\n"
                    "- Weak evidence becomes a repair queue item, not a primary "
                    "entry reason."
                ),
                "Contradictions": (
                    "- If a page has many bullish facts but weak evidence quality, "
                    "Jue must separate attractive narrative from executable proof."
                ),
                "Open Questions": (
                    "- Which parser or source is creating the most warnings?\n"
                    "- Which high-opportunity symbols are blocked by weak evidence?\n"
                    "- Which warnings should become crawler/parser repair tasks?"
                ),
                "Next Context Pack Summary": (
                    f"{clean_scope} evidence quality: {summary_line}. "
                    "Treat partial/weak source layers as trust discounts and repair "
                    "inputs before block sizing."
                ),
                "Coverage Matrix": "\n".join(type_lines)
                or "- No source type has quality-tagged refs yet.",
                "Freshness": (
                    "- Quality status is derived from latest wiki source refs at "
                    "rebuild time; stale warnings remain visible until source pages "
                    "are rebuilt with cleaner evidence."
                ),
                "Data Gaps": "\n".join(top_warning_lines)
                or "- No evidence quality warnings recorded.",
                "Actionability": "\n".join(affected_lines)
                or "- No affected page requires evidence-quality action yet.",
            },
            source_refs=source_refs,
            confidence=0.82 if rows else 0.52,
            freshness="fresh",
        )
        return 0

    def _rebuild_repair_queue_page(self, *, scope: str) -> int:
        clean_scope = _normalize_scope(scope)
        rows = self._repair_queue_rows(scope=clean_scope)
        if not rows:
            return 0
        open_statuses = {"scheduled", "unresolved"}
        open_rows = [
            row for row in rows if str(row.get("status") or "") in open_statuses
        ]
        resolved_rows = [
            row for row in rows if str(row.get("status") or "") == "resolved"
        ]
        open_rows_sorted = sorted(
            open_rows,
            key=self._repair_queue_open_row_sort_key,
        )
        resolved_action_ids = {str(row.get("action_id") or "") for row in resolved_rows}
        open_action_ids = {str(row.get("action_id") or "") for row in open_rows}
        other_rows = [
            row
            for row in rows
            if str(row.get("action_id") or "") not in open_action_ids
            and str(row.get("action_id") or "") not in resolved_action_ids
        ]
        source_ref_rows = [*open_rows_sorted, *resolved_rows, *other_rows]
        status_counts: dict[str, int] = {}
        action_counts: dict[str, int] = {}
        warning_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            action_type = str(row.get("action_type") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            action_counts[action_type] = action_counts.get(action_type, 0) + 1
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            for warning in list(details.get("quality_warnings") or []):
                clean_warning = str(warning).strip()
                if clean_warning:
                    warning_counts[clean_warning] = (
                        warning_counts.get(clean_warning, 0) + 1
                    )
        action_batches = self._repair_queue_action_batches(
            [
                {
                    **row,
                    "scope": clean_scope,
                    "count": 1,
                }
                for row in open_rows
            ]
        )

        def count_lines(values: dict[str, int], *, label: str) -> list[str]:
            return [
                f"- {label}={key}, count={count}"
                for key, count in sorted(
                    values.items(),
                    key=lambda item: (-int(item[1]), item[0]),
                )
            ]

        open_lines: list[str] = []
        for row in open_rows_sorted[:16]:
            details = dict(row.get("details") or {})
            warnings = ", ".join(
                str(item)
                for item in list(details.get("quality_warnings") or [])[:5]
                if str(item).strip()
            )
            reasons = " | ".join(
                str(item)
                for item in list(details.get("reasons") or [])[:3]
                if str(item).strip()
            )
            diagnostic_reasons = " | ".join(
                str(item)
                for item in list(
                    details.get("diagnostic_reasons") or details.get("reasons") or []
                )[:3]
                if str(item).strip()
            )
            symbols = ",".join(
                str(symbol)
                for symbol in list(row.get("symbols") or [])[:6]
                if str(symbol).strip()
            )
            impacted_pages = ",".join(
                str(item)
                for item in list(details.get("impacted_page_ids") or [])[:6]
                if str(item).strip()
            )
            recommended_actions = ",".join(
                str(target.get("recommended_action") or "")
                for target in list(details.get("repair_targets") or [])[:6]
                if isinstance(target, dict)
                and str(target.get("recommended_action") or "").strip()
            )
            source_id = str(details.get("source_id") or "").strip()
            source_status = str(details.get("source_status") or "").strip()
            open_lines.append(
                "- action_id={action_id}, page={page_id}, symbols={symbols}, "
                "source={source_id}, source_status={source_status}, "
                "action_type={action_type}, status={status}, warnings={warnings}, "
                "repair_action={repair_action}, impacted_pages={impacted_pages}, "
                "recommended_actions={recommended_actions}, "
                "diagnostic_reasons={diagnostic_reasons}, "
                "quality_effectiveness_reasons={reasons}".format(
                    action_id=row.get("action_id") or "-",
                    page_id=row.get("page_id") or "-",
                    symbols=symbols or "-",
                    source_id=source_id or "-",
                    source_status=source_status or "-",
                    action_type=row.get("action_type") or "-",
                    status=row.get("status") or "-",
                    warnings=warnings or "-",
                    repair_action=details.get("repair_action") or "-",
                    impacted_pages=impacted_pages or "-",
                    recommended_actions=recommended_actions or "-",
                    diagnostic_reasons=diagnostic_reasons or "-",
                    reasons=reasons or "-",
                )
            )
        resolved_lines: list[str] = []
        for row in resolved_rows[:12]:
            details = dict(row.get("details") or {})
            symbols = ",".join(
                str(symbol)
                for symbol in list(row.get("symbols") or [])[:6]
                if str(symbol).strip()
            )
            resolved_lines.append(
                "- action_id={action_id}, page={page_id}, symbols={symbols}, "
                "action_type={action_type}, resolved_by={resolved_by}, "
                "finished_at={finished_at}".format(
                    action_id=row.get("action_id") or "-",
                    page_id=row.get("page_id") or "-",
                    symbols=symbols or "-",
                    action_type=row.get("action_type") or "-",
                    resolved_by=details.get("resolved_by") or "-",
                    finished_at=row.get("finished_at") or "-",
                )
            )
        batch_lines = [
            "- action_type={action_type}, count={count}, symbols={symbols}, "
            "warnings={warnings}, recommended_actions={recommended_actions}".format(
                action_type=batch.get("action_type") or "-",
                count=batch.get("count") or 0,
                symbols=",".join(
                    str(symbol)
                    for symbol in list(batch.get("symbols") or [])[:12]
                    if str(symbol).strip()
                )
                or "-",
                warnings=",".join(
                    str(warning)
                    for warning in list(batch.get("warnings") or [])[:8]
                    if str(warning).strip()
                )
                or "-",
                recommended_actions=",".join(
                    str(action)
                    for action in list(batch.get("recommended_actions") or [])[:8]
                    if str(action).strip()
                )
                or "-",
            )
            for batch in action_batches
        ]
        source_refs = [
            {
                "source_type": "wiki_repair_queue",
                "source_id": str(row.get("action_id") or ""),
                "source_scope": clean_scope,
                "source_path": str(row.get("page_id") or ""),
                "observed_at": str(row.get("created_at") or ""),
                "action_type": str(row.get("action_type") or ""),
                "status": str(row.get("status") or ""),
                "symbols": [
                    str(symbol)
                    for symbol in list(row.get("symbols") or [])[:8]
                    if str(symbol).strip()
                ],
                "quality_warnings": [
                    str(warning)
                    for warning in list(
                        dict(row.get("details") or {}).get("quality_warnings") or []
                    )[:8]
                    if str(warning).strip()
                ],
                "repair_action": str(
                    dict(row.get("details") or {}).get("repair_action") or ""
                ),
                "decision_scope": str(
                    dict(row.get("details") or {}).get("decision_scope") or ""
                ),
                "closed_block_outcomes_without_horizon": (
                    dict(row.get("details") or {}).get(
                        "closed_block_outcomes_without_horizon"
                    )
                    or 0
                ),
                "closed_block_outcomes_without_horizon_pct": (
                    dict(row.get("details") or {}).get(
                        "closed_block_outcomes_without_horizon_pct"
                    )
                    or 0.0
                ),
                "diagnostic_reasons": [
                    str(reason)
                    for reason in list(
                        dict(row.get("details") or {}).get("reasons") or []
                    )[:8]
                    if str(reason).strip()
                ],
                "impacted_page_ids": [
                    str(item)
                    for item in list(
                        dict(row.get("details") or {}).get("impacted_page_ids") or []
                    )[:12]
                    if str(item).strip()
                ],
                "impacted_symbols": [
                    str(item)
                    for item in list(
                        dict(row.get("details") or {}).get("impacted_symbols") or []
                    )[:24]
                    if str(item).strip()
                ],
                "repair_targets": [
                    {
                        key: str(target.get(key) or "")
                        for key in ("page_id", "symbol", "recommended_action")
                        if str(target.get(key) or "").strip()
                    }
                    for target in list(
                        dict(row.get("details") or {}).get("repair_targets") or []
                    )[:12]
                    if isinstance(target, dict)
                ],
            }
            for row in source_ref_rows[:64]
        ]
        open_symbols = sorted(
            {
                str(symbol)
                for row in open_rows
                for symbol in list(row.get("symbols") or [])
                if str(symbol).strip()
            }
        )
        self.write_page(
            scope=clean_scope,
            page_type="research",
            key="repair_queue",
            title=f"{clean_scope.upper()} Repair Queue",
            symbols=open_symbols,
            content_sections={
                "Current Stance": (
                    f"{clean_scope} repair queue: open_action_count="
                    f"{len(open_rows)}, resolved_action_count={len(resolved_rows)}. "
                    "Jue should treat open repair actions as source-quality tasks, "
                    "not as broad no-trade bans."
                ),
                "Durable Facts": "\n".join(
                    [
                        f"- scope={clean_scope}",
                        f"- total_repair_action_count={len(rows)}",
                        f"- open_action_count={len(open_rows)}",
                        f"- resolved_action_count={len(resolved_rows)}",
                        f"- open_symbols={','.join(open_symbols) if open_symbols else '-'}",
                    ]
                ),
                "Evidence Links": "\n".join(
                    f"- wiki_repair_queue:{ref['source_id']}"
                    for ref in source_refs[:32]
                )
                or "- No repair actions linked.",
                "Trading History": (
                    "- Repair queue is not a trade outcome by itself; it records "
                    "evidence/data weaknesses that must be resolved or cross-checked."
                ),
                "Lessons": (
                    "- Open source-quality repairs require smaller probes, waiting "
                    "blocks, or direct data refresh before size increases.\n"
                    "- Resolved repairs show the wiki learning loop closed after "
                    "fresh data arrived."
                ),
                "Contradictions": (
                    "- A symbol can remain attractive while still carrying a repair "
                    "task; separate alpha thesis from evidence completeness."
                ),
                "Open Questions": (
                    "- Which repair action blocks mid/long sizing?\n"
                    "- Which repair action can be solved by a fresh crawl?\n"
                    "- Which unresolved parser issue should become code repair?"
                ),
                "Next Context Pack Summary": (
                    f"{clean_scope} repair queue open={len(open_rows)}, "
                    f"resolved={len(resolved_rows)}, open_symbols="
                    f"{','.join(open_symbols[:12]) if open_symbols else '-'}. "
                    "Use open actions as precise follow-up checks."
                ),
                "Coverage Matrix": "\n".join(
                    [
                        *count_lines(status_counts, label="status"),
                        *count_lines(action_counts, label="action_type"),
                        *count_lines(warning_counts, label="warning"),
                    ]
                )
                or "- No repair status counts.",
                "Action Batches": "\n".join(batch_lines)
                or "- No open action batch.",
                "Freshness": (
                    "- Repair queue reflects wiki_repair_actions at rebuild time; "
                    "resolved actions remain as provenance for the learning loop."
                ),
                "Data Gaps": "\n".join(open_lines)
                or "- No open repair action.",
                "Actionability": "\n".join(resolved_lines)
                or "- No resolved repair action yet.",
            },
            source_refs=source_refs,
            confidence=0.84 if open_rows else 0.72,
            freshness="fresh",
        )
        return 1

    @staticmethod
    def _repair_queue_open_row_sort_key(row: dict[str, Any]) -> tuple[int, str, str]:
        action_type = str(row.get("action_type") or "")
        status = str(row.get("status") or "")
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        warnings = {
            str(item).strip()
            for item in list(details.get("quality_warnings") or [])
            if str(item).strip()
        }
        status_rank = {"scheduled": 0, "unresolved": 1}.get(status, 2)
        if (
            action_type.startswith("repair_research_source_")
            or action_type in {
                "inspect_research_source_coverage",
                "populate_research_source_rows",
                "restore_research_source_db",
            }
            or "research_coverage_unhealthy" in warnings
        ):
            action_rank = 0
        elif action_type in {
            "reproject_closed_block_outcome_horizons",
            "repair_quality_warning_effectiveness",
            "repair_usage_guidance_contract",
        }:
            action_rank = 1
        elif (
            action_type == "refresh_requested_symbol_summary"
            or "requested_symbol_summary_missing" in warnings
        ):
            action_rank = 2
        elif (
            action_type == "refresh_symbol_financials"
            or "financials_missing" in warnings
        ):
            action_rank = 3
        else:
            action_rank = 4
        return (status_rank, action_rank, str(row.get("created_at") or ""))

    def _repair_queue_rows(self, *, scope: str) -> list[dict[str, Any]]:
        clean_scope = _normalize_scope(scope)
        if not clean_scope:
            return []
        with self._connect() as conn:
            if not self._table_exists(conn, "wiki_repair_actions"):
                return []
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                ORDER BY
                    CASE status
                        WHEN 'scheduled' THEN 0
                        WHEN 'unresolved' THEN 1
                        WHEN 'resolved' THEN 2
                        ELSE 3
                    END,
                    created_at DESC,
                    action_id DESC
                LIMIT 200
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        prefix = f"{clean_scope}."
        for row in rows:
            page_id = str(row["page_id"] or "")
            details = self._parse_json(
                row["details_json"],
                {},
                field=f"wiki_repair_actions.details_json:{row['action_id']}",
                allow_missing=True,
            )
            if not isinstance(details, dict):
                details = {}
            row_scope = str(details.get("decision_scope") or "").strip().lower()
            if not page_id.startswith(prefix) and row_scope != clean_scope:
                continue
            raw_symbols = details.get("symbols") if isinstance(details, dict) else []
            raw_impacted_symbols = (
                details.get("impacted_symbols") if isinstance(details, dict) else []
            )
            symbols = [
                _normalize_symbol(symbol)
                for symbol in [*list(raw_symbols or []), *list(raw_impacted_symbols or [])]
                if str(symbol).strip()
            ]
            raw_repair_targets = (
                details.get("repair_targets") if isinstance(details, dict) else []
            )
            for target in list(raw_repair_targets or []):
                if not isinstance(target, dict):
                    continue
                target_symbol = str(target.get("symbol") or "").strip()
                if target_symbol:
                    symbols.append(_normalize_symbol(target_symbol))
            symbol_prefix = f"{clean_scope}.symbol."
            page_symbol = (
                page_id.rsplit(".", 1)[-1]
                if page_id.startswith(symbol_prefix) and "." in page_id
                else ""
            )
            if page_symbol:
                symbols.append(_normalize_symbol(page_symbol))
            result.append(
                {
                    "action_id": str(row["action_id"] or ""),
                    "finding_id": str(row["finding_id"] or ""),
                    "page_id": page_id,
                    "action_type": str(row["action_type"] or ""),
                    "status": str(row["status"] or ""),
                    "details": details,
                    "symbols": [
                        symbol
                        for symbol in dict.fromkeys(symbols)
                        if str(symbol).strip()
                    ],
                    "created_at": str(row["created_at"] or ""),
                    "finished_at": str(row["finished_at"] or ""),
                    "error_message": str(row["error_message"] or ""),
                }
            )
        return result

    def _evidence_quality_rows(
        self,
        *,
        scope: str,
    ) -> tuple[list[dict[str, Any]], int, int]:
        clean_scope = _normalize_scope(scope)
        rows: list[dict[str, Any]] = []
        total_source_refs = 0
        with self._connect() as conn:
            page_rows = conn.execute(
                """
                SELECT page_id, title, page_type, symbols_json, source_refs_json
                FROM wiki_pages
                WHERE scope = ? AND status = 'active'
                  AND page_id != ?
                ORDER BY page_id ASC
                """,
                (clean_scope, f"{clean_scope}.research.evidence_quality"),
            ).fetchall()
        for page in page_rows:
            page_id = str(page["page_id"] or "")
            symbols = [
                _normalize_symbol(symbol)
                for symbol in self._parse_json(
                    page["symbols_json"],
                    [],
                    field=f"wiki_pages.symbols_json:{page_id}",
                    allow_missing=True,
                )
                if str(symbol).strip()
            ]
            refs = self._parse_json(
                page["source_refs_json"],
                [],
                field=f"wiki_pages.source_refs_json:{page_id}",
                allow_missing=True,
            )
            if not isinstance(refs, list):
                continue
            refs = self._flatten_source_refs(refs)
            total_source_refs += len(refs)
            for ref in refs:
                if not isinstance(ref, dict):
                    continue
                status = _normalize_quality_status(ref.get("quality_status"))
                warnings = [
                    str(item).strip()
                    for item in list(ref.get("quality_warnings") or [])
                    if str(item).strip()
                ]
                evidence_quality = (
                    ref.get("evidence_quality")
                    if isinstance(ref.get("evidence_quality"), dict)
                    else {}
                )
                if not status:
                    status = self._quality_status_from_evidence_quality(
                        evidence_quality
                    )
                if not warnings:
                    warnings = self._quality_warnings_from_evidence_quality(
                        evidence_quality
                    )
                if not status and not warnings:
                    continue
                rows.append(
                    {
                        "page_id": page_id,
                        "title": str(page["title"] or page_id),
                        "page_type": str(page["page_type"] or ""),
                        "symbols": symbols,
                        "source_type": str(ref.get("source_type") or ""),
                        "source_id": str(ref.get("source_id") or ""),
                        "source_scope": str(ref.get("source_scope") or clean_scope),
                        "observed_at": str(ref.get("observed_at") or ""),
                        "quality_status": status or "unknown",
                        "quality_warnings": warnings,
                        "evidence_quality": evidence_quality,
                    }
                )
        return rows, total_source_refs, len(page_rows)

    def _research_coverage_rows(self, *, scope: str) -> list[dict[str, Any]]:
        return [
            self._research_coverage_row(spec)
            for spec in self._research_coverage_specs(scope=scope)
        ]

    def _research_coverage_specs(self, *, scope: str) -> list[dict[str, Any]]:
        clean_scope = _normalize_scope(scope)
        if clean_scope == "kis":
            return [
                {
                    "source_id": "naver_reports",
                    "path": self.config.naver_reports_db_path,
                    "tables": [
                        ("reports", ("published_at", "created_at", "updated_at")),
                        ("report_symbol_links", ("updated_at", "created_at")),
                        ("report_chunks", ("created_at",)),
                    ],
                },
                {
                    "source_id": "symbol_fundamentals",
                    "path": self.config.symbol_fundamentals_db_path,
                    "tables": [
                        ("valuation_snapshots", ("crawled_at", "as_of")),
                        ("valuation_scores", ("scored_at",)),
                        ("financial_snapshots", ("crawled_at",)),
                    ],
                },
                {
                    "source_id": "etf_research",
                    "path": self.config.etf_research_db_path,
                    "tables": [
                        ("etf_market_snapshots", ("captured_at",)),
                        ("etf_scores", ("scored_at",)),
                        ("etf_universe", ("updated_at",)),
                    ],
                },
                {
                    "source_id": "strategy_insights",
                    "path": self.config.strategy_insights_db_path,
                    "tables": [("strategy_signals", ("as_of", "updated_at"))],
                },
                {
                    "source_id": "daily_discovery",
                    "path": self.config.daily_discovery_db_path,
                    "tables": [
                        ("discovery_runs", ("run_at", "created_at")),
                        ("discovery_samples", ("updated_at", "created_at")),
                    ],
                },
                {
                    "source_id": "market_pulse",
                    "path": self.config.market_pulse_db_path,
                    "tables": [("market_pulse_snapshots", ("captured_at",))],
                },
            ]
        if clean_scope == "binance":
            return [
                {
                    "source_id": "crypto_market_research",
                    "path": self.config.crypto_market_research_db_path,
                    "tables": [
                        ("crypto_market_snapshots", ("captured_at",)),
                        ("crypto_research_runs", ("run_at",)),
                        ("crypto_candidates", ("updated_at",)),
                        ("crypto_symbol_notes", ("updated_at",)),
                    ],
                },
                {
                    "source_id": "crypto_quant",
                    "path": self.config.crypto_quant_db_path,
                    "tables": [
                        ("crypto_quant_signals", ("updated_at",)),
                        ("crypto_quant_signal_history", ("captured_at",)),
                    ],
                },
                {
                    "source_id": "crypto_pattern_lab",
                    "path": self.config.crypto_pattern_lab_db_path,
                    "tables": [
                        ("optimized_strategy_sets", ("promoted_at",)),
                        ("optimization_runs", ("finished_at", "created_at")),
                        ("pattern_backtests", ("sample_end",)),
                    ],
                },
                {
                    "source_id": "crypto_alpha",
                    "path": self.config.crypto_alpha_db_path,
                    "tables": [
                        ("crypto_alpha_events", ("detected_at", "event_time")),
                        ("crypto_alpha_snapshots", ("crawled_at",)),
                        ("crypto_alpha_event_symbols", ("validity_checked_at",)),
                    ],
                },
            ]
        return []

    def _research_coverage_row(self, spec: dict[str, Any]) -> dict[str, Any]:
        source_id = str(spec.get("source_id") or "")
        path_value = spec.get("path")
        if path_value is None:
            return self._empty_research_coverage_row(
                source_id=source_id,
                path="",
                status="missing_path",
                reason="path not configured",
            )
        source_path = Path(path_value)
        if not source_path.exists():
            return self._empty_research_coverage_row(
                source_id=source_id,
                path=str(source_path),
                status="missing_db",
                reason="db file missing",
            )
        details: list[dict[str, Any]] = []
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                for table_name, time_columns in list(spec.get("tables") or []):
                    table = str(table_name)
                    if not self._table_exists(conn, table):
                        details.append(
                            {
                                "table": table,
                                "status": "missing_table",
                                "rows": 0,
                                "symbols": 0,
                                "latest_at": "",
                            }
                        )
                        continue
                    columns = self._table_columns(conn, table)
                    count = int(
                        conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                            "count"
                        ]
                        or 0
                    )
                    symbol_count = 0
                    if "symbol" in columns:
                        symbol_count = int(
                            conn.execute(
                                f"""
                                SELECT COUNT(DISTINCT symbol) AS count
                                FROM {table}
                                WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                                """
                            ).fetchone()["count"]
                            or 0
                        )
                    details.append(
                        {
                            "table": table,
                            "status": "ok",
                            "rows": count,
                            "symbols": symbol_count,
                            "latest_at": self._latest_value_for_table(
                                conn,
                                table=table,
                                columns=columns,
                                candidates=tuple(time_columns or ()),
                            ),
                        }
                    )
        except sqlite3.DatabaseError as exc:
            return self._empty_research_coverage_row(
                source_id=source_id,
                path=str(source_path),
                status="error",
                reason=str(exc),
                tables=details,
            )
        usable = [row for row in details if row.get("status") == "ok"]
        primary = usable[0] if usable else {}
        latest_values = [
            str(row.get("latest_at") or "")
            for row in usable
            if str(row.get("latest_at") or "")
        ]
        row_count = int(primary.get("rows") or 0)
        return {
            "source_id": source_id,
            "path": str(source_path),
            "status": "ok" if row_count > 0 else "empty",
            "reason": "" if row_count > 0 else "primary table has no rows",
            "rows": row_count,
            "symbols": int(primary.get("symbols") or 0),
            "latest_at": max(latest_values) if latest_values else "",
            "primary_table": str(primary.get("table") or ""),
            "tables": details,
        }

    @staticmethod
    def _empty_research_coverage_row(
        *,
        source_id: str,
        path: str,
        status: str,
        reason: str,
        tables: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "path": path,
            "status": status,
            "reason": reason,
            "rows": 0,
            "symbols": 0,
            "latest_at": "",
            "primary_table": "",
            "tables": list(tables or []),
        }

    @staticmethod
    def _latest_value_for_table(
        conn: sqlite3.Connection,
        *,
        table: str,
        columns: set[str],
        candidates: tuple[str, ...],
    ) -> str:
        for column in candidates:
            if column not in columns:
                continue
            row = conn.execute(
                f"SELECT MAX(NULLIF({column}, '')) AS latest FROM {table}"
            ).fetchone()
            latest = str(row["latest"] or "") if row is not None else ""
            if latest:
                return latest
        return ""

    def _rebuild_market_pulse_regime_page(self) -> int:
        pulse = self._read_latest_market_pulse()
        if not pulse:
            return 0
        pulse_id = str(pulse.get("id") or "latest")
        self.write_page(
            scope="kis",
            page_type="regime",
            key="market_pulse",
            title="KIS Market Pulse",
            symbols=[],
            content_sections=self._build_market_pulse_sections(pulse),
            source_refs=[
                {
                    "source_type": "market_pulse",
                    "source_id": pulse_id,
                    "source_scope": "kis",
                    "observed_at": str(pulse.get("captured_at") or ""),
                }
            ],
            confidence=0.74,
            freshness="fresh",
        )
        return 1

    def _read_latest_market_pulse(self) -> dict[str, Any] | None:
        path = self.config.market_pulse_db_path
        if path is None:
            return None
        source_path = Path(path)
        if not source_path.exists():
            return None
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "market_pulse_snapshots"):
                    return None
                row = conn.execute(
                    """
                    SELECT id, captured_at, trading_day, status, regime, score,
                           indices_json, sector_json, block_alignment_json,
                           risk_flags_json, data_gaps_json
                    FROM market_pulse_snapshots
                    ORDER BY captured_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read market pulse DB in {source_path}: {exc}"
            ) from exc
        if row is None:
            return None
        payload = {key: row[key] for key in row.keys()}
        for key in (
            "indices_json",
            "sector_json",
            "block_alignment_json",
            "risk_flags_json",
            "data_gaps_json",
        ):
            payload[key.removesuffix("_json")] = self._parse_source_json_any(
                payload.get(key),
                fallback=[],
                field=f"market_pulse_snapshots.{key}:{payload.get('id')}",
            )
        return payload

    def _build_market_pulse_sections(self, pulse: dict[str, Any]) -> dict[str, str]:
        indices = [
            row for row in list(pulse.get("indices") or []) if isinstance(row, dict)
        ]
        sectors = pulse.get("sector")
        sector_rows = (
            [row for row in sectors if isinstance(row, dict)]
            if isinstance(sectors, list)
            else [sectors]
            if isinstance(sectors, dict)
            else []
        )
        risks = [
            str(item).strip()
            for item in list(pulse.get("risk_flags") or [])[:8]
            if str(item).strip()
        ]
        gaps = [
            str(item).strip()
            for item in list(pulse.get("data_gaps") or [])[:8]
            if str(item).strip()
        ]
        index_lines = [
            "- {code}: value={value}, change_pct={change}, direction={direction}".format(
                code=row.get("code") or row.get("name") or "-",
                value=row.get("value") or "-",
                change=row.get("change_pct") or "-",
                direction=row.get("direction") or "-",
            )
            for row in indices[:8]
        ]
        sector_lines = [
            "- {name}: score={score}, change_pct={change}, direction={direction}".format(
                name=row.get("name") or row.get("sector") or row.get("label") or "-",
                score=row.get("score") or row.get("strength") or "-",
                change=row.get("change_pct") or row.get("return_pct") or "-",
                direction=row.get("direction") or "-",
            )
            for row in sector_rows[:10]
        ]
        next_summary = (
            "KIS 판단 시 market_pulse regime={regime}, score={score}, "
            "risk_flags={risk_count}, data_gaps={gap_count}를 먼저 보고 "
            "장 전체 방향과 섹터 순환을 블록 horizon/entry posture에 반영한다."
        ).format(
            regime=pulse.get("regime") or "-",
            score=pulse.get("score") or "-",
            risk_count=len(risks),
            gap_count=len(gaps),
        )
        return {
            "Current Stance": (
                "국장 쥬는 시장펄스를 장 전체 레짐과 섹터 순환의 압축 기억으로 사용한다."
            ),
            "Durable Facts": "\n".join(
                [
                    f"- trading_day={pulse.get('trading_day') or ''}",
                    f"- captured_at={pulse.get('captured_at') or ''}",
                    f"- status={pulse.get('status') or ''}",
                    f"- regime={pulse.get('regime') or ''}",
                    f"- score={pulse.get('score') or ''}",
                ]
            ),
            "Evidence Links": f"- market_pulse:{pulse.get('id') or 'latest'}",
            "Current Markers": "\n".join(index_lines) or "- No index markers.",
            "Risk Posture": "\n".join(risks) or "- No explicit risk flag.",
            "Useful Playbooks": "\n".join(sector_lines) or "- No sector marker.",
            "Invalidated Playbooks": "\n".join(gaps) or "- No data gap.",
            "Next Context Pack Summary": next_summary,
        }

    def _rag_research_lines(
        self,
        *,
        symbol: str,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if self.rag_store is None:
            return [], []
        try:
            query_fn = self.rag_store.query
            signature = inspect.signature(query_fn)
            parameters = signature.parameters
            accepts_kwargs = any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in parameters.values()
            )
            if accepts_kwargs or "limit" in parameters:
                rows = query_fn(symbol, limit=4)
            elif "top_k" in parameters:
                rows = query_fn(symbol, top_k=4)
            else:
                rows = query_fn(symbol)
        except Exception as exc:
            return [f"- rag_error:{type(exc).__name__}:{str(exc)[:160]}"], []
        lines: list[str] = []
        refs: list[dict[str, Any]] = []
        for row in list(rows or [])[:4]:
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            text = str(row.get("text") or row.get("content") or "").strip()
            report_id = str(
                metadata.get("report_id")
                or metadata.get("id")
                or row.get("report_id")
                or row.get("doc_id")
                or ""
            )
            title = str(metadata.get("title") or row.get("title") or "").strip()
            if text:
                label = f" {title}" if title else ""
                lines.append(f"- RAG{label}: {text[:280]}")
            if report_id:
                refs.append(
                    {
                        "source_type": "rag",
                        "source_id": report_id,
                        "source_scope": "research",
                    }
                )
                lines.append(f"- rag:{report_id}")
        return lines, refs

    def _rebuild_binance_symbols(self) -> int:
        blocks = self._read_binance_symbol_blocks()
        reflections = self._read_reflections_by_symbol(scope="binance")
        crypto_research = self._read_crypto_research_by_symbol()
        manager_observations = self._read_binance_manager_observations_by_symbol()
        self._record_manager_observation_repair_actions(
            scope="binance",
            observations_by_symbol=manager_observations,
        )
        self._resolve_manager_observation_repair_actions(
            scope="binance",
            observations_by_symbol=manager_observations,
        )
        quant_notes = self._read_crypto_quant_by_symbol()
        pattern_notes = self._read_crypto_pattern_lab_by_symbol()
        alpha_events = self._read_crypto_alpha_by_symbol()
        self._deactivate_invalid_binance_symbol_pages()
        updated = 0
        for symbol in sorted(
            set(blocks)
            | set(reflections)
            | set(crypto_research)
            | set(manager_observations)
            | set(quant_notes)
            | set(pattern_notes)
            | set(alpha_events)
        ):
            symbol_blocks = blocks.get(symbol, [])[:16]
            symbol_reflections = reflections.get(symbol, [])[:12]
            symbol_research = crypto_research.get(symbol, {})
            symbol_observations = manager_observations.get(symbol, [])[:12]
            symbol_quant = quant_notes.get(symbol, [])[:8]
            symbol_patterns = pattern_notes.get(symbol, [])[:8]
            symbol_alpha = alpha_events.get(symbol, [])[:6]
            if (
                not symbol_blocks
                and not symbol_reflections
                and not symbol_research
                and not symbol_observations
                and not symbol_quant
                and not symbol_patterns
                and not symbol_alpha
            ):
                continue
            source_refs = [
                {
                    "source_type": "binance_blocks",
                    "source_id": str(row.get("block_id") or ""),
                    "source_scope": "binance",
                    "observed_at": str(
                        row.get("closed_at") or row.get("created_at") or ""
                    ),
                }
                for row in symbol_blocks
                if str(row.get("block_id") or "").strip()
            ] + [
                {
                    "source_type": "investment_memory",
                    "source_id": str(row.get("block_id") or ""),
                    "source_scope": "binance",
                    "observed_at": str(row.get("created_at") or ""),
                }
                for row in symbol_reflections
                if str(row.get("block_id") or "").strip()
            ] + [
                {
                    "source_type": "binance_manager_runs",
                    "source_id": str(row.get("manager_run_id") or ""),
                    "source_scope": "binance",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_observations
                if str(row.get("manager_run_id") or "").strip()
            ] + [
                {
                    "source_type": "crypto_quant",
                    "source_id": str(row.get("source_id") or ""),
                    "source_scope": "research",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_quant
                if str(row.get("source_id") or "").strip()
            ] + [
                {
                    "source_type": "crypto_pattern_lab",
                    "source_id": str(row.get("set_id") or row.get("pattern_id") or ""),
                    "source_scope": "research",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_patterns
                if str(row.get("set_id") or row.get("pattern_id") or "").strip()
            ] + [
                {
                    "source_type": "crypto_alpha",
                    "source_id": str(row.get("event_id") or ""),
                    "source_scope": "research",
                    "observed_at": str(row.get("observed_at") or ""),
                }
                for row in symbol_alpha
                if str(row.get("event_id") or "").strip()
            ]
            research_lines, research_refs = self._crypto_research_lines_and_refs(
                symbol=symbol,
                research=symbol_research,
            )
            content_sections = self._build_binance_symbol_sections(
                symbol=symbol,
                blocks=symbol_blocks,
                reflections=symbol_reflections,
                manager_observations=symbol_observations,
                quant_signals=symbol_quant,
                pattern_notes=symbol_patterns,
                alpha_events=symbol_alpha,
            )
            if research_lines:
                content_sections["Evidence Links"] = "\n".join(
                    [
                        content_sections.get("Evidence Links", ""),
                        "### Crypto Market Research",
                        *research_lines,
                    ]
                ).strip()
            source_refs.extend(research_refs)
            self.write_page(
                scope="binance",
                page_type="symbol",
                key=symbol,
                title=symbol,
                symbols=[symbol],
                content_sections=content_sections,
                source_refs=source_refs,
                confidence=0.68
                if symbol_reflections
                else 0.56
                if symbol_research or symbol_quant or symbol_patterns or symbol_alpha
                else 0.48,
                freshness="fresh",
            )
            updated += 1
        return updated

    def _read_crypto_research_by_symbol(self) -> dict[str, dict[str, list[dict[str, Any]]]]:
        path = self.config.crypto_market_research_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if self._table_exists(conn, "crypto_symbol_notes"):
                    note_rows = conn.execute(
                        """
                        SELECT symbol, stance, horizon, confidence, summary_md,
                               reasons_json, risks_json, triggers_json, updated_at
                        FROM crypto_symbol_notes
                        WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                        ORDER BY updated_at DESC
                        LIMIT 320
                        """
                    ).fetchall()
                    for row in note_rows:
                        payload = {key: row[key] for key in row.keys()}
                        symbol = _normalize_symbol(str(payload.get("symbol") or ""))
                        if not _is_crypto_tradable_symbol(symbol):
                            continue
                        payload["reasons"] = self._safe_json_list(
                            payload.get("reasons_json"),
                            field=f"crypto_symbol_notes.reasons_json:{symbol}",
                        )
                        payload["risks"] = self._safe_json_list(
                            payload.get("risks_json"),
                            field=f"crypto_symbol_notes.risks_json:{symbol}",
                        )
                        payload["triggers"] = self._safe_json_list(
                            payload.get("triggers_json"),
                            field=f"crypto_symbol_notes.triggers_json:{symbol}",
                        )
                        grouped.setdefault(symbol, {}).setdefault("notes", []).append(
                            payload
                        )
                if self._table_exists(conn, "crypto_candidates"):
                    candidate_rows = conn.execute(
                        """
                        SELECT symbol, market, stance, horizon, score, confidence,
                               reason_md, block_template_json, source_run_id, updated_at
                        FROM crypto_candidates
                        WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                        ORDER BY updated_at DESC, score DESC
                        LIMIT 480
                        """
                    ).fetchall()
                    for row in candidate_rows:
                        payload = {key: row[key] for key in row.keys()}
                        symbol = _normalize_symbol(str(payload.get("symbol") or ""))
                        if not _is_crypto_tradable_symbol(symbol):
                            continue
                        template = self._parse_source_json_object(
                            payload.get("block_template_json"),
                            field=f"crypto_candidates.block_template_json:{symbol}",
                        )
                        payload["block_template"] = template
                        grouped.setdefault(symbol, {}).setdefault(
                            "candidates", []
                        ).append(payload)
        except JueWikiSourceReadError:
            raise
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read crypto market research DB in {source_path}: {exc}"
            ) from exc
        return grouped

    def _read_crypto_quant_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.crypto_quant_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "crypto_quant_signals"):
                    return {}
                rows = conn.execute(
                    """
                    SELECT symbol, horizon, long_score, short_score, no_trade_score,
                           expected_r_long, expected_r_short, signal_json, updated_at
                    FROM crypto_quant_signals
                    WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                    ORDER BY updated_at DESC,
                             MAX(long_score, short_score) DESC
                    LIMIT 720
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read crypto quant DB in {source_path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not _is_crypto_tradable_symbol(symbol):
                continue
            signal = self._parse_source_json_object(
                payload.get("signal_json"),
                field=f"crypto_quant_signals.signal_json:{symbol}:{payload.get('horizon')}",
            )
            payload["symbol"] = symbol
            payload["signal"] = signal
            payload["source_id"] = f"{symbol}:{payload.get('horizon') or 'latest'}"
            payload["observed_at"] = str(payload.get("updated_at") or "")
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _read_crypto_pattern_lab_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.crypto_pattern_lab_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "optimized_strategy_sets"):
                    return {}
                rows = conn.execute(
                    """
                    SELECT set_id, run_id, trial_id, pattern_id, symbol, interval,
                           family, direction, parameter_set_json, objective,
                           objective_score, trade_count, win_rate, expectancy_r,
                           profit_factor, max_loss_r, out_of_sample_trade_count,
                           out_of_sample_expectancy_r,
                           out_of_sample_profit_factor,
                           out_of_sample_max_drawdown_r, overfit_risk,
                           status, promoted_at
                    FROM optimized_strategy_sets
                    WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                    ORDER BY COALESCE(NULLIF(promoted_at, ''), '') DESC,
                             objective_score DESC
                    LIMIT 720
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read crypto pattern lab DB in {source_path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not _is_crypto_tradable_symbol(symbol):
                continue
            payload["symbol"] = symbol
            payload["parameters"] = self._parse_source_json_object(
                payload.get("parameter_set_json"),
                field=f"optimized_strategy_sets.parameter_set_json:{payload.get('set_id')}",
            )
            payload["observed_at"] = str(payload.get("promoted_at") or "")
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _read_crypto_alpha_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.crypto_alpha_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not (
                    self._table_exists(conn, "crypto_alpha_event_symbols")
                    and self._table_exists(conn, "crypto_alpha_events")
                ):
                    return {}
                rows = conn.execute(
                    """
                    SELECT
                        es.event_id,
                        es.symbol,
                        es.impact_direction,
                        es.impact_horizon,
                        es.link_confidence,
                        es.reason,
                        es.validity_status,
                        es.validity_reason,
                        e.event_type,
                        e.title,
                        e.summary,
                        e.event_time,
                        e.detected_at,
                        e.confidence,
                        e.importance,
                        e.status
                    FROM crypto_alpha_event_symbols AS es
                    JOIN crypto_alpha_events AS e
                      ON e.event_id = es.event_id
                    WHERE es.symbol IS NOT NULL
                      AND TRIM(es.symbol) != ''
                      AND COALESCE(es.validity_status, '') != 'invalid'
                    ORDER BY COALESCE(NULLIF(e.detected_at, ''), '') DESC,
                             e.importance DESC
                    LIMIT 360
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read crypto alpha DB in {source_path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if not _is_crypto_tradable_symbol(symbol):
                continue
            payload["symbol"] = symbol
            payload["observed_at"] = str(
                payload.get("detected_at") or payload.get("event_time") or ""
            )
            grouped.setdefault(symbol, []).append(payload)
        return grouped

    def _manager_blocks_db_path(self, *, scope: str) -> Path | None:
        clean_scope = _normalize_scope(scope)
        if clean_scope == "kis":
            return self.config.kis_blocks_db_path
        if clean_scope == "binance":
            return self.config.binance_blocks_db_path
        return None

    def _read_manager_run_ops_events(self, *, scope: str) -> list[dict[str, Any]]:
        source_path = self._manager_blocks_db_path(scope=scope)
        if source_path is None or not Path(source_path).exists():
            return []
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                rows: list[sqlite3.Row] = []
                for table in ("manager_runs", "manager_runs_archive"):
                    if not self._table_exists(conn, table):
                        continue
                    columns = self._table_columns(conn, table)
                    status_expr = "status" if "status" in columns else "'' AS status"
                    error_expr = (
                        "error_message"
                        if "error_message" in columns
                        else "'' AS error_message"
                    )
                    session_expr = (
                        "market_session"
                        if "market_session" in columns
                        else "'' AS market_session"
                    )
                    mode_expr = "mode" if "mode" in columns else "'' AS mode"
                    model_expr = "model" if "model" in columns else "'' AS model"
                    rows.extend(
                        conn.execute(
                            f"""
                            SELECT id, run_at, {session_expr}, {mode_expr}, {model_expr},
                                   {status_expr}, {error_expr},
                                   prompt_json, response_json, actions_json,
                                   '{table}' AS source_table
                            FROM {table}
                            ORDER BY run_at DESC, id DESC
                            LIMIT 120
                            """
                        ).fetchall()
                    )
                rows.sort(
                    key=lambda row: (str(row["run_at"] or ""), int(row["id"] or 0)),
                    reverse=True,
                )
                rows = rows[:180]
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read {scope} manager_runs ops in {source_path}: {exc}"
            ) from exc

        def parsed(
            raw: str | None,
            *,
            field: str,
            parse_errors: list[str],
        ) -> dict[str, Any]:
            try:
                value = self._parse_json(raw, {}, field=field, allow_missing=True)
            except JueWikiDataIntegrityError:
                parse_errors.append(f"invalid_json:{field}")
                return {}
            return value if isinstance(value, dict) else {}

        def action_count(value: dict[str, Any]) -> int:
            total = 0
            for key in ("create_blocks", "update_blocks", "close_blocks", "pause_blocks"):
                items = value.get(key)
                if isinstance(items, list):
                    total += len(items)
            return total

        events: list[dict[str, Any]] = []
        for row in rows:
            run_id = str(row["id"] or "")
            source_table = str(row["source_table"] or "manager_runs")
            parse_errors: list[str] = []
            prompt = parsed(
                row["prompt_json"],
                field=f"{source_table}.prompt_json:{run_id}",
                parse_errors=parse_errors,
            )
            response = parsed(
                row["response_json"],
                field=f"{source_table}.response_json:{run_id}",
                parse_errors=parse_errors,
            )
            actions = parsed(
                row["actions_json"],
                field=f"{source_table}.actions_json:{run_id}",
                parse_errors=parse_errors,
            )
            prompt_budget = (
                prompt.get("prompt_budget")
                if isinstance(prompt.get("prompt_budget"), dict)
                else {}
            )
            storage_compaction = (
                prompt.get("_storage_compaction")
                if isinstance(prompt.get("_storage_compaction"), dict)
                else {}
            )
            error_message = str(row["error_message"] or "").strip()
            status = str(row["status"] or "").strip().lower()
            has_error = (
                bool(error_message)
                or bool(parse_errors)
                or status not in {"", "ok", "success"}
            )
            over_max = bool(prompt_budget.get("over_max"))
            emergency = bool(storage_compaction.get("emergency"))
            if not (has_error or over_max or emergency):
                continue
            hold_decision = (
                response.get("hold_decision")
                if isinstance(response.get("hold_decision"), dict)
                else response.get("no_action_watch")
                if isinstance(response.get("no_action_watch"), dict)
                else {}
            )
            events.append(
                {
                    "manager_run_id": run_id,
                    "source_table": source_table,
                    "source_type": (
                        f"{scope}_manager_runs_archive"
                        if source_table == "manager_runs_archive"
                        else f"{scope}_manager_runs"
                    ),
                    "run_at": str(row["run_at"] or ""),
                    "market_session": str(row["market_session"] or ""),
                    "mode": str(row["mode"] or ""),
                    "model": str(row["model"] or ""),
                    "status": status or "unknown",
                    "error_message": (
                        error_message or "; ".join(parse_errors)
                    )[:260],
                    "parse_errors": parse_errors,
                    "prompt_total_chars": prompt_budget.get("total_chars"),
                    "prompt_max_chars": prompt_budget.get("max_chars"),
                    "prompt_over_max": over_max,
                    "storage_emergency": emergency,
                    "storage_original_chars": storage_compaction.get("original_chars"),
                    "storage_priority_reason": str(
                        storage_compaction.get("priority_reason") or ""
                    ).strip()[:120],
                    "storage_dropped_keys": [
                        str(item).strip()[:80]
                        for item in (
                            storage_compaction.get("dropped_keys")
                            if isinstance(
                                storage_compaction.get("dropped_keys"),
                                list,
                            )
                            else []
                        )[:8]
                        if str(item).strip()
                    ],
                    "storage_dropped_key_count": storage_compaction.get(
                        "dropped_key_count"
                    ),
                    "action_count": action_count(actions),
                    "hold_summary": str(
                        hold_decision.get("summary")
                        or hold_decision.get("hold_summary")
                        or ""
                    ).strip()[:220],
                }
            )
        return events

    def _rebuild_manager_run_ops_page(self, *, scope: str) -> int:
        events = self._read_manager_run_ops_events(scope=scope)
        if not events:
            return 0
        title = f"{scope.upper()} Manager Run Operations"
        selected_events = self._select_manager_run_ops_events(events)
        event_lines: list[str] = []
        evidence_lines: list[str] = []
        for event in selected_events:
            error_text = self._format_ops_text(event.get("error_message") or "-")
            event_lines.append(
                "- manager_run={run_id}, at={run_at}, status={status}, "
                "error={error}, prompt_chars={chars}/{max_chars}, "
                "storage_emergency={emergency}, priority={priority}, "
                "dropped={dropped}, actions={actions}, hold={hold}".format(
                    run_id=event.get("manager_run_id") or "-",
                    run_at=event.get("run_at") or "-",
                    status=event.get("status") or "-",
                    error=error_text,
                    chars=self._format_ops_number(
                        event.get("prompt_total_chars")
                        or event.get("storage_original_chars")
                    ),
                    max_chars=self._format_ops_number(event.get("prompt_max_chars")),
                    emergency=event.get("storage_emergency"),
                    priority=self._format_ops_text(
                        event.get("storage_priority_reason") or "-"
                    ),
                    dropped=",".join(
                        [
                            self._format_ops_text(key)
                            for key in (
                                event.get("storage_dropped_keys")
                                if isinstance(
                                    event.get("storage_dropped_keys"),
                                    list,
                                )
                                else []
                            )
                        ]
                    )
                    or "-",
                    actions=event.get("action_count"),
                    hold=self._format_ops_text(event.get("hold_summary") or "-"),
                )
            )
            evidence_lines.append(
                f"- {event.get('source_type') or f'{scope}_manager_runs'}:"
                f"{event.get('manager_run_id') or '-'}"
            )
        recent_errors = [event for event in events if event.get("error_message")]
        source_refs = [
            {
                "source_type": str(
                    event.get("source_type") or f"{scope}_manager_runs"
                ),
                "source_id": str(event.get("manager_run_id") or ""),
                "source_scope": scope,
                "observed_at": str(event.get("run_at") or ""),
            }
            for event in selected_events
            if str(event.get("manager_run_id") or "").strip()
        ]
        self.write_page(
            scope=scope,
            page_type="ops",
            key="manager_runs",
            title=title,
            symbols=[],
            content_sections={
                "Current Stance": (
                    f"{scope} manager run failures, prompt budget pressure, and "
                    "emergency compaction are operational repair memory for Jue."
                ),
                "Durable Facts": (
                    f"- scope={scope}\n"
                    f"- recent_error_count={len(recent_errors)}\n"
                    f"- tracked_event_count={len(events)}"
                ),
                "Evidence Links": "\n".join(evidence_lines) or "- No linked evidence.",
                "Trading History": "\n".join(
                    ["### Manager Run Operations", *event_lines]
                ),
                "Lessons": (
                    "- Manager run errors are repair memory, not reasons to become passive.\n"
                    "- prompt_budget_exceeded or emergency compaction means the next prompt "
                    "must prefer compact Jue Wiki, selected candidates, and executable "
                    "repair tasks over raw context bloat.\n"
                    "- Native SDK timeouts must be converted into smaller context packs, "
                    "not silent holds."
                ),
                "Contradictions": (
                    "- If action pressure is high but action_count remains zero, identify "
                    "whether the blocker is price geometry, live authority, prompt budget, "
                    "or missing evidence."
                ),
                "Open Questions": (
                    "- Which context section is growing fastest?\n"
                    "- Did the latest manager run see the current trading_validation page?\n"
                    "- Are blocked candidates being converted into wait/probe blocks or "
                    "explicit candidate-level rejection notes?"
                ),
                "Next Context Pack Summary": (
                    f"{scope} ops memory: recent manager errors={len(recent_errors)}, "
                    f"events={len(events)}. Treat failures as repair tasks while keeping "
                    "safety gates authoritative."
                ),
            },
            source_refs=source_refs,
            confidence=0.78,
            freshness="fresh",
        )
        return 1

    def _rebuild_action_pressure_page(self, *, scope: str) -> int:
        clean_scope = _normalize_scope(scope)
        events = self._read_action_pressure_events(scope=clean_scope)
        if not events:
            return 0
        selected_events = events[:24]
        recent_run_count = len(events)
        no_action_count = sum(1 for event in events if event.get("no_action"))
        action_count = sum(1 for event in events if not event.get("no_action"))
        unresolved_count = sum(
            1
            for event in events
            if event.get("no_action")
            and (
                int(event.get("candidate_count") or 0) > 0
                or str(event.get("pressure_status") or "") == "action_required"
            )
        )
        candidate_total = sum(int(event.get("candidate_count") or 0) for event in events)
        strong_candidate_total = sum(
            int(event.get("strong_candidate_count") or 0) for event in events
        )
        zero_streak_max = max(
            [int(event.get("zero_action_streak") or 0) for event in events] or [0]
        )
        event_lines: list[str] = []
        resolution_lines: list[str] = []
        source_refs: list[dict[str, Any]] = []
        for event in selected_events:
            event_lines.append(
                "- manager_run={run_id}, at={run_at}, status={status}, "
                "no_action={no_action}, requested_actions={requested}, "
                "applied_actions={applied}, candidates={candidates}, "
                "strong={strong}, pressure={pressure}/{level}, streak={streak}, "
                "hold={hold}, required={required}".format(
                    run_id=event.get("manager_run_id") or "-",
                    run_at=event.get("run_at") or "-",
                    status=event.get("status") or "-",
                    no_action=event.get("no_action"),
                    requested=event.get("requested_action_count") or 0,
                    applied=event.get("applied_action_count") or 0,
                    candidates=event.get("candidate_count") or 0,
                    strong=event.get("strong_candidate_count") or 0,
                    pressure=event.get("pressure_status") or "-",
                    level=event.get("pressure_level") or "-",
                    streak=event.get("zero_action_streak") or 0,
                    hold=self._format_ops_text(event.get("hold_summary") or "-"),
                    required=self._format_ops_text(
                        event.get("required_resolution") or "-"
                    ),
                )
            )
            if event.get("no_action") and (
                int(event.get("candidate_count") or 0) > 0
                or str(event.get("pressure_status") or "") == "action_required"
            ):
                resolution_lines.append(
                    "- manager_run={run_id}: unresolved candidate pressure must "
                    "become a probe/waiting block, a precise price trigger, or a "
                    "candidate-level rejection reason.".format(
                        run_id=event.get("manager_run_id") or "-"
                    )
                )
            source_refs.append(
                {
                    "source_type": f"{clean_scope}_manager_runs",
                    "source_id": str(event.get("manager_run_id") or ""),
                    "source_scope": clean_scope,
                    "observed_at": str(event.get("run_at") or ""),
                }
            )
        durable_facts = "\n".join(
            [
                f"- scope={clean_scope}",
                f"- recent_run_count={recent_run_count}",
                f"- no_action_run_count={no_action_count}",
                f"- action_run_count={action_count}",
                f"- unresolved_pressure_count={unresolved_count}",
                f"- candidate_count_total={candidate_total}",
                f"- strong_candidate_count_total={strong_candidate_total}",
                f"- zero_action_streak_max={zero_streak_max}",
            ]
        )
        self.write_page(
            scope=clean_scope,
            page_type="ops",
            key="action_pressure",
            title=f"{clean_scope.upper()} Action Pressure",
            symbols=[],
            content_sections={
                "Current Stance": (
                    f"{clean_scope} action pressure is Jue's anti-passivity memory: "
                    "visible candidates must be resolved into executable blocks, "
                    "waiting triggers, or explicit candidate-level rejection."
                ),
                "Durable Facts": durable_facts,
                "Evidence Links": "\n".join(
                    f"- {ref['source_type']}:{ref['source_id']}"
                    for ref in source_refs
                )
                or "- No linked evidence.",
                "Trading History": "\n".join(event_lines)
                or "- No recent manager pressure.",
                "Lessons": (
                    "- No-action with candidates is unresolved trading work, not a "
                    "stable resting state.\n"
                    "- If validation is probe/readiness, Jue should reduce size and "
                    "tighten entry quality rather than erase the candidate.\n"
                    "- Repeated holds must produce either a better price structure or "
                    "a named reject condition."
                ),
                "Contradictions": (
                    "- Broad research coverage plus repeated no-action indicates an "
                    "evidence-to-execution gap."
                ),
                "Open Questions": (
                    "- Which candidate has enough evidence for a small probe/waiting block?\n"
                    "- Which hold reason can be rewritten as a concrete trigger?\n"
                    "- Which rejection reason is specific enough to improve the next scan?"
                ),
                "Next Context Pack Summary": (
                    f"{clean_scope} action pressure: no_action={no_action_count}/"
                    f"{recent_run_count}, unresolved={unresolved_count}, "
                    f"candidates={candidate_total}, strong={strong_candidate_total}, "
                    f"zero_streak_max={zero_streak_max}. Resolve backlog into "
                    "probe/waiting block or explicit reject."
                ),
                "Action Pressure": "\n".join(event_lines)
                or "- No active pressure.",
                "Resolution Queue": "\n".join(resolution_lines)
                or "- No unresolved pressure queue.",
                "Probe Mandate": (
                    "- probe/waiting block is the default response when candidates "
                    "exist but scale-up evidence is weak.\n"
                    "- Safety gates still decide execution; this page prevents silent "
                    "passivity when candidates and research are present."
                ),
            },
            source_refs=source_refs,
            confidence=0.84 if unresolved_count else 0.72,
            freshness="fresh",
        )
        return 1

    def _rebuild_opportunity_pipeline_page(self, *, scope: str) -> int:
        clean_scope = _normalize_scope(scope)
        payload = self._read_opportunity_pipeline(scope=clean_scope)
        candidates = list(payload.get("candidates") or [])
        missed = list(payload.get("missed_upside") or [])
        creative = list(payload.get("creative_hypotheses") or [])
        if not (candidates or missed or creative):
            return 0

        candidate_lines: list[str] = []
        missed_lines: list[str] = []
        creative_lines: list[str] = []
        source_refs: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, str]] = set()

        def add_ref(run_id: str, observed_at: str) -> None:
            if not run_id:
                return
            source_type = f"{clean_scope}_manager_runs"
            key = (source_type, run_id)
            if key in seen_refs:
                return
            seen_refs.add(key)
            source_refs.append(
                {
                    "source_type": source_type,
                    "source_id": run_id,
                    "source_scope": clean_scope,
                    "observed_at": observed_at,
                }
            )

        for row in candidates[:36]:
            run_id = str(row.get("manager_run_id") or "").strip()
            add_ref(run_id, str(row.get("run_at") or ""))
            candidate_lines.append(
                "- manager_run={run_id}, at={run_at}, source={source}, "
                "symbol={symbol}, name={name}, market={market}, lane={lane}, "
                "side={side}, horizon={horizon}, score={score}, confidence={confidence}, "
                "stance={stance}, entry={entry}, target={target}, stop={stop}, "
                "signals={signals}, summary={summary}".format(
                    run_id=run_id or "-",
                    run_at=row.get("run_at") or "-",
                    source=row.get("source_type") or "-",
                    symbol=row.get("symbol") or "-",
                    name=row.get("name") or "-",
                    market=row.get("market") or "-",
                    lane=row.get("lane") or "-",
                    side=row.get("side") or "-",
                    horizon=row.get("horizon") or "-",
                    score=row.get("score") or "-",
                    confidence=row.get("confidence") or "-",
                    stance=row.get("stance") or "-",
                    entry=row.get("entry") or "-",
                    target=row.get("target") or "-",
                    stop=row.get("stop") or "-",
                    signals=", ".join(
                        str(item) for item in list(row.get("signals") or [])[:5]
                    )
                    or "-",
                    summary=self._format_ops_text(row.get("summary") or "-")[:260],
                )
            )

        for row in missed[:18]:
            run_id = str(row.get("manager_run_id") or "").strip()
            add_ref(run_id, str(row.get("run_at") or ""))
            missed_lines.append(
                "- manager_run={run_id}, at={run_at}, symbol={symbol}, name={name}, "
                "move_pct={move}, reason={reason}".format(
                    run_id=run_id or "-",
                    run_at=row.get("run_at") or "-",
                    symbol=row.get("symbol") or "-",
                    name=row.get("name") or "-",
                    move=row.get("move_pct") or row.get("upside_pct") or "-",
                    reason=self._format_ops_text(
                        row.get("miss_reason")
                        or row.get("reason")
                        or row.get("summary")
                        or "-"
                    )[:260],
                )
            )

        for row in creative[:18]:
            run_id = str(row.get("manager_run_id") or "").strip()
            add_ref(run_id, str(row.get("run_at") or ""))
            creative_lines.append(
                "- manager_run={run_id}, at={run_at}, symbol={symbol}, idea={idea}, "
                "next_trigger={trigger}".format(
                    run_id=run_id or "-",
                    run_at=row.get("run_at") or "-",
                    symbol=row.get("symbol") or "-",
                    idea=self._format_ops_text(row.get("idea") or row.get("summary") or "-")[
                        :260
                    ],
                    trigger=self._format_ops_text(
                        row.get("next_trigger")
                        or row.get("trigger")
                        or row.get("condition")
                        or "-"
                    )[:220],
                )
            )

        candidate_symbols = {
            str(row.get("symbol") or "").strip()
            for row in candidates
            if str(row.get("symbol") or "").strip()
        }
        candidate_count = len(candidate_symbols)
        candidate_observation_count = len(candidates)
        missed_count = len(missed)
        creative_count = len(creative)
        durable_facts = "\n".join(
            [
                f"- scope={clean_scope}",
                f"- manager_run_count={payload.get('run_count') or 0}",
                f"- candidate_backlog_count={candidate_count}",
                f"- candidate_observation_count={candidate_observation_count}",
                f"- missed_upside_count={missed_count}",
                f"- creative_hypothesis_count={creative_count}",
            ]
        )
        self.write_page(
            scope=clean_scope,
            page_type="ops",
            key="opportunity_pipeline",
            title=f"{clean_scope.upper()} Opportunity Pipeline",
            symbols=[],
            content_sections={
                "Current Stance": (
                    f"{clean_scope} opportunity pipeline is Jue's active "
                    "profit-seeking memory. Candidate backlog, missed upside, and "
                    "creative hypotheses must become executable waiting/probe blocks "
                    "or explicit candidate-level rejects."
                ),
                "Durable Facts": durable_facts,
                "Evidence Links": "\n".join(
                    f"- {ref['source_type']}:{ref['source_id']}"
                    for ref in source_refs
                )
                or "- No linked evidence.",
                "Trading History": "\n".join(candidate_lines)
                or "- No candidate backlog.",
                "Lessons": (
                    "- Repeated pre-surge or volatile candidates are research fuel "
                    "for small, executable waiting/probe block designs.\n"
                    "- Missed upside is not a reason to chase blindly; convert the "
                    "miss into entry geometry, confirmation, or a named reject.\n"
                    "- Creative hypotheses should preserve optionality across "
                    "low-point, pullback, breakout, ETF/core, spot, and futures lanes."
                ),
                "Contradictions": (
                    "- If research and candidates exist but no block appears, the "
                    "problem is evidence-to-execution translation, not a lack of ideas."
                ),
                "Open Questions": (
                    "- Which backlog candidate can become a low-risk waiting/probe block?\n"
                    "- Which missed move exposes a repeatable pre-surge clue?\n"
                    "- Which hypothesis needs live authority, spread, orderbook, or "
                    "valuation confirmation before execution?"
                ),
                "Next Context Pack Summary": (
                    f"{clean_scope} opportunity pipeline: candidates={candidate_count}, "
                    f"missed_upside={missed_count}, creative={creative_count}. "
                    "Resolve at least one high-quality opportunity into a waiting/probe "
                    "block, or write a precise candidate-level reject."
                ),
                "Action Pressure": (
                    "- Opportunity backlog strengthens action pressure when safety "
                    "gates and live authority are open."
                ),
                "Opportunity Pipeline": "\n".join(candidate_lines)
                or "- No candidate backlog.",
                "Missed Upside": "\n".join(missed_lines)
                or "- No missed upside reviews.",
                "Creative Hypotheses": "\n".join(creative_lines)
                or "- No creative hypotheses.",
                "Resolution Queue": (
                    "- Convert one candidate into a waiting/probe block if entry, "
                    "target, stop, and validation are present.\n"
                    "- Otherwise record the exact missing price structure, data gap, "
                    "or rejection condition."
                ),
                "Probe Mandate": (
                    "- waiting/probe block is the default output for promising but "
                    "not-yet-scale-ready opportunities."
                ),
            },
            source_refs=source_refs,
            confidence=0.82 if candidate_count else 0.7,
            freshness="fresh",
        )
        return 1

    def _read_opportunity_pipeline(self, *, scope: str) -> dict[str, Any]:
        clean_scope = _normalize_scope(scope)
        source_path = self._manager_runs_db_path(scope=clean_scope)
        if source_path is None or not source_path.exists():
            return {"candidates": [], "missed_upside": [], "creative_hypotheses": []}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "manager_runs"):
                    return {
                        "candidates": [],
                        "missed_upside": [],
                        "creative_hypotheses": [],
                    }
                columns = self._table_columns(conn, "manager_runs")
                status_expr = "status" if "status" in columns else "'' AS status"
                error_expr = (
                    "error_message"
                    if "error_message" in columns
                    else "'' AS error_message"
                )
                session_expr = (
                    "market_session"
                    if "market_session" in columns
                    else "'' AS market_session"
                )
                mode_expr = "mode" if "mode" in columns else "'' AS mode"
                model_expr = "model" if "model" in columns else "'' AS model"
                rows = conn.execute(
                    f"""
                    SELECT id, run_at, {session_expr}, {mode_expr}, {model_expr},
                           {status_expr}, {error_expr},
                           prompt_json, response_json, actions_json
                    FROM manager_runs
                    ORDER BY run_at DESC, id DESC
                    LIMIT 80
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read {clean_scope} opportunity pipeline in {source_path}: {exc}"
            ) from exc

        candidates: list[dict[str, Any]] = []
        missed_upside: list[dict[str, Any]] = []
        creative_hypotheses: list[dict[str, Any]] = []
        seen_candidates: set[tuple[str, str, str]] = set()
        seen_missed: set[tuple[str, str, str]] = set()
        seen_creative: set[tuple[str, str, str]] = set()

        def list_items(value: Any, *, key: str = "") -> list[Any]:
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get(key) if key else value.get("items")
                if isinstance(nested, list):
                    return nested
                nested = value.get("candidates")
                return nested if isinstance(nested, list) else []
            return []

        def candidate_symbol(candidate: dict[str, Any]) -> str:
            symbol = _normalize_symbol(str(candidate.get("symbol") or ""))
            if clean_scope == "kis":
                return symbol if re.fullmatch(r"\d{6}", symbol) else ""
            if clean_scope == "binance":
                return symbol if _is_crypto_tradable_symbol(symbol) else ""
            return symbol

        def add_candidate(
            candidate: Any,
            *,
            row: sqlite3.Row,
            source_type: str,
        ) -> None:
            if not isinstance(candidate, dict):
                return
            symbol = candidate_symbol(candidate)
            if not symbol:
                return
            run_id = str(row["id"] or "")
            key = (run_id, source_type, symbol)
            if key in seen_candidates:
                return
            seen_candidates.add(key)
            calc = candidate.get("calculated") if isinstance(candidate.get("calculated"), dict) else {}
            signals = candidate.get("signals") if isinstance(candidate.get("signals"), list) else []
            sources = candidate.get("sources") if isinstance(candidate.get("sources"), list) else []
            entry_value = (
                candidate.get("entry_trigger_price")
                if candidate.get("entry_trigger_price") is not None
                else candidate.get("entry_price")
                if candidate.get("entry_price") is not None
                else candidate.get("price")
            )
            candidates.append(
                {
                    "manager_run_id": run_id,
                    "run_at": str(row["run_at"] or ""),
                    "source_type": source_type,
                    "symbol": symbol,
                    "name": str(candidate.get("name") or symbol),
                    "market": str(candidate.get("market") or row["market_session"] or "").strip(),
                    "lane": str(candidate.get("lane") or calc.get("lane") or "").strip(),
                    "side": str(candidate.get("side") or "").strip(),
                    "horizon": str(candidate.get("horizon") or "").strip(),
                    "score": candidate.get("aggressive_score")
                    if candidate.get("aggressive_score") is not None
                    else candidate.get("score"),
                    "confidence": candidate.get("confidence"),
                    "stance": str(candidate.get("stance") or "").strip(),
                    "entry": entry_value,
                    "target": candidate.get("target_price"),
                    "stop": candidate.get("stop_price"),
                    "signals": signals or sources,
                    "summary": str(
                        candidate.get("summary")
                        or candidate.get("reason")
                        or candidate.get("reason_md")
                        or candidate.get("condition")
                        or ""
                    ).strip(),
                }
            )

        def add_missed(item: Any, *, row: sqlite3.Row, source_type: str) -> None:
            if not isinstance(item, dict):
                return
            symbol = candidate_symbol(item)
            if not symbol:
                return
            run_id = str(row["id"] or "")
            reason = str(
                item.get("miss_reason")
                or item.get("reason")
                or item.get("summary")
                or ""
            ).strip()
            key = (run_id, source_type, f"{symbol}:{reason[:80]}")
            if key in seen_missed:
                return
            seen_missed.add(key)
            missed_upside.append(
                {
                    "manager_run_id": run_id,
                    "run_at": str(row["run_at"] or ""),
                    "symbol": symbol,
                    "name": str(item.get("name") or symbol),
                    "move_pct": item.get("move_pct") or item.get("upside_pct"),
                    "miss_reason": reason,
                }
            )

        def add_creative(item: Any, *, row: sqlite3.Row, source_type: str) -> None:
            if not isinstance(item, dict):
                return
            symbol = candidate_symbol(item)
            if not symbol:
                return
            run_id = str(row["id"] or "")
            idea = str(item.get("idea") or item.get("summary") or "").strip()
            key = (run_id, source_type, f"{symbol}:{idea[:80]}")
            if key in seen_creative:
                return
            seen_creative.add(key)
            creative_hypotheses.append(
                {
                    "manager_run_id": run_id,
                    "run_at": str(row["run_at"] or ""),
                    "symbol": symbol,
                    "idea": idea,
                    "next_trigger": item.get("next_trigger")
                    or item.get("trigger")
                    or item.get("condition"),
                }
            )

        for row in rows:
            run_id = str(row["id"] or "")
            prompt = self._parse_manager_run_json(
                row["prompt_json"],
                field=f"manager_runs.prompt_json:{run_id}",
            )
            response = self._parse_manager_run_json(
                row["response_json"],
                field=f"manager_runs.response_json:{run_id}",
            )
            actions = self._parse_manager_run_json(
                row["actions_json"],
                field=f"manager_runs.actions_json:{run_id}",
            )
            proactive_pressure = (
                prompt.get("proactive_decision_pressure")
                if isinstance(prompt.get("proactive_decision_pressure"), dict)
                else {}
            )
            hold_decision = self._manager_hold_decision(response)
            aggressive = (
                prompt.get("aggressive_opportunities")
                if isinstance(prompt.get("aggressive_opportunities"), dict)
                else {}
            )
            opportunity = (
                prompt.get("opportunity_research_brief")
                if isinstance(prompt.get("opportunity_research_brief"), dict)
                else {}
            )
            latest_input = (
                response.get("latest_input_summary")
                if isinstance(response.get("latest_input_summary"), dict)
                else {}
            )
            for candidate in list_items(aggressive, key="candidates")[:12]:
                add_candidate(
                    candidate,
                    row=row,
                    source_type="aggressive_opportunities",
                )
            for candidate in list_items(proactive_pressure, key="top_candidates")[:12]:
                add_candidate(
                    candidate,
                    row=row,
                    source_type="proactive_decision_pressure",
                )
            for candidate in list_items(latest_input, key="aggressive_top")[:12]:
                add_candidate(candidate, row=row, source_type="latest_input_summary")
            for key, source_type in (
                ("pre_surge_candidates", "opportunity_research_brief.pre_surge"),
                ("block_candidates", "opportunity_research_brief.block_candidate"),
                (
                    "daily_discovery_candidates",
                    "opportunity_research_brief.daily_discovery",
                ),
                ("aggressive_candidates", "opportunity_research_brief.aggressive"),
            ):
                for candidate in list_items(opportunity, key=key)[:12]:
                    add_candidate(candidate, row=row, source_type=source_type)
            for candidate in list_items(prompt.get("candidates"))[:18]:
                add_candidate(candidate, row=row, source_type="manager_candidates")
            for candidate in list_items(hold_decision, key="next_triggers")[:12]:
                add_candidate(
                    candidate,
                    row=row,
                    source_type="hold_decision.next_trigger",
                )
            for candidate in list_items(actions, key="create_blocks")[:12]:
                add_candidate(candidate, row=row, source_type="manager_create_blocks")
            for item in list_items(prompt.get("missed_upside_reviews"))[:12]:
                add_missed(item, row=row, source_type="prompt.missed_upside_reviews")
            for item in list_items(response.get("missed_upside_reviews"))[:12]:
                add_missed(item, row=row, source_type="response.missed_upside_reviews")
            for item in list_items(prompt.get("creative_hypotheses"))[:12]:
                add_creative(item, row=row, source_type="prompt.creative_hypotheses")
            for item in list_items(response.get("creative_hypotheses"))[:12]:
                add_creative(item, row=row, source_type="response.creative_hypotheses")

        return {
            "run_count": len(rows),
            "candidates": candidates,
            "missed_upside": missed_upside,
            "creative_hypotheses": creative_hypotheses,
        }

    def _read_action_pressure_events(self, *, scope: str) -> list[dict[str, Any]]:
        clean_scope = _normalize_scope(scope)
        source_path = self._manager_runs_db_path(scope=clean_scope)
        if source_path is None or not source_path.exists():
            return []
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "manager_runs"):
                    return []
                columns = self._table_columns(conn, "manager_runs")
                status_expr = "status" if "status" in columns else "'' AS status"
                error_expr = (
                    "error_message"
                    if "error_message" in columns
                    else "'' AS error_message"
                )
                session_expr = (
                    "market_session"
                    if "market_session" in columns
                    else "'' AS market_session"
                )
                mode_expr = "mode" if "mode" in columns else "'' AS mode"
                model_expr = "model" if "model" in columns else "'' AS model"
                rows = conn.execute(
                    f"""
                    SELECT id, run_at, {session_expr}, {mode_expr}, {model_expr},
                           {status_expr}, {error_expr},
                           prompt_json, response_json, actions_json
                    FROM manager_runs
                    ORDER BY run_at DESC, id DESC
                    LIMIT 80
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read {clean_scope} action pressure in {source_path}: {exc}"
            ) from exc
        events: list[dict[str, Any]] = []
        for row in rows:
            run_id = str(row["id"] or "")
            prompt = self._parse_manager_run_json(
                row["prompt_json"],
                field=f"manager_runs.prompt_json:{run_id}",
            )
            response = self._parse_manager_run_json(
                row["response_json"],
                field=f"manager_runs.response_json:{run_id}",
            )
            actions = self._parse_manager_run_json(
                row["actions_json"],
                field=f"manager_runs.actions_json:{run_id}",
            )
            hold_decision = self._manager_hold_decision(response)
            proactive_pressure = (
                prompt.get("proactive_decision_pressure")
                if isinstance(prompt.get("proactive_decision_pressure"), dict)
                else {}
            )
            requested_action_count = self._manager_action_count(actions)
            applied_action_count = self._manager_applied_action_count(
                actions.get("_applied")
                if isinstance(actions.get("_applied"), dict)
                else response.get("applied")
                if isinstance(response.get("applied"), dict)
                else {}
            )
            candidate_count = int(
                self._manager_number(proactive_pressure.get("candidate_count"))
                or len(list(proactive_pressure.get("top_candidates") or []))
                or len(list(hold_decision.get("watch_symbols") or []))
                or len(list(hold_decision.get("next_triggers") or []))
            )
            strong_candidate_count = int(
                self._manager_number(
                    proactive_pressure.get("strong_candidate_count")
                )
                or 0
            )
            no_action = requested_action_count <= 0 and applied_action_count <= 0
            pressure_status = str(proactive_pressure.get("status") or "").strip()
            if not (
                no_action
                or requested_action_count > 0
                or applied_action_count > 0
                or candidate_count > 0
                or pressure_status
                or hold_decision
            ):
                continue
            events.append(
                {
                    "manager_run_id": run_id,
                    "run_at": str(row["run_at"] or ""),
                    "market_session": str(row["market_session"] or ""),
                    "mode": str(row["mode"] or ""),
                    "model": str(row["model"] or ""),
                    "status": str(row["status"] or ""),
                    "error_message": str(row["error_message"] or "")[:260],
                    "requested_action_count": requested_action_count,
                    "applied_action_count": applied_action_count,
                    "no_action": no_action,
                    "candidate_count": candidate_count,
                    "strong_candidate_count": strong_candidate_count,
                    "pressure_status": pressure_status,
                    "pressure_level": str(
                        proactive_pressure.get("pressure_level") or ""
                    ).strip(),
                    "zero_action_streak": int(
                        self._manager_number(
                            proactive_pressure.get("zero_action_streak")
                        )
                        or 0
                    ),
                    "required_resolution": str(
                        proactive_pressure.get("required_resolution") or ""
                    ).strip()[:260],
                    "hold_summary": str(
                        hold_decision.get("summary")
                        or hold_decision.get("hold_summary")
                        or ""
                    ).strip()[:260],
                }
            )
        return events

    def _manager_runs_db_path(self, *, scope: str) -> Path | None:
        clean_scope = _normalize_scope(scope)
        if clean_scope == "kis":
            return self.config.kis_blocks_db_path
        if clean_scope == "binance":
            return self.config.binance_blocks_db_path
        return None

    def _parse_manager_run_json(self, raw: str | None, *, field: str) -> dict[str, Any]:
        try:
            value = self._parse_json(raw, {}, field=field, allow_missing=True)
        except JueWikiDataIntegrityError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _manager_hold_decision(response: dict[str, Any]) -> dict[str, Any]:
        for key in ("hold_decision", "no_action_watch"):
            value = response.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _manager_action_count(actions: dict[str, Any]) -> int:
        total = 0
        for key in (
            "create_blocks",
            "update_blocks",
            "close_blocks",
            "pause_blocks",
            "adopt_existing_blocks",
        ):
            items = actions.get(key)
            if isinstance(items, list):
                total += len(items)
        return total

    @staticmethod
    def _manager_applied_action_count(applied: dict[str, Any]) -> int:
        total = 0
        for value in applied.values() if isinstance(applied, dict) else []:
            if isinstance(value, dict):
                total += int(JueWikiService._manager_number(value.get("item_count")) or 0)
            elif isinstance(value, list):
                total += len(value)
        return total

    @staticmethod
    def _manager_number(value: Any) -> float:
        if value in (None, "", [], {}):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _select_manager_run_ops_events(
        events: list[dict[str, Any]],
        *,
        limit: int = 24,
        archive_reserve: int = 6,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if len(events) <= limit:
            return list(events)

        def is_archive(event: dict[str, Any]) -> bool:
            source_table = str(event.get("source_table") or "")
            source_type = str(event.get("source_type") or "")
            return source_table == "manager_runs_archive" or source_type.endswith(
                "_manager_runs_archive"
            )

        def key(event: dict[str, Any]) -> tuple[str, str, str, str]:
            return (
                str(event.get("source_table") or event.get("source_type") or ""),
                str(event.get("manager_run_id") or ""),
                str(event.get("run_at") or ""),
                str(event.get("error_message") or ""),
            )

        archive_events = [event for event in events if is_archive(event)]
        if not archive_events:
            return list(events[:limit])

        archive_take = min(len(archive_events), max(1, min(archive_reserve, limit)))
        current_limit = max(0, limit - archive_take)
        selected_keys: set[tuple[str, str, str, str]] = set()
        for event in (event for event in events if not is_archive(event)):
            if len(selected_keys) >= current_limit:
                break
            selected_keys.add(key(event))
        archive_selected_count = 0
        for event in archive_events:
            if archive_selected_count >= archive_take:
                break
            event_key = key(event)
            if event_key in selected_keys:
                continue
            selected_keys.add(event_key)
            archive_selected_count += 1
        for event in events:
            if len(selected_keys) >= limit:
                break
            selected_keys.add(key(event))
        return [event for event in events if key(event) in selected_keys][:limit]

    @staticmethod
    def _format_ops_number(value: Any) -> str:
        if value in (None, "", [], {}):
            return "-"
        try:
            return f"{int(float(value)):,}"
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _format_ops_text(cls, value: Any) -> str:
        text = str(value or "")

        def replace(match: re.Match[str]) -> str:
            return f"{int(match.group(0)):,}"

        return re.sub(r"(?<![A-Za-z0-9])\d{6,}(?![A-Za-z0-9])", replace, text)

    def _read_binance_manager_observations_by_symbol(
        self,
    ) -> dict[str, list[dict[str, Any]]]:
        path = self.config.binance_blocks_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "manager_runs"):
                    return {}
                columns = self._table_columns(conn, "manager_runs")
                status_expr = "status" if "status" in columns else "'' AS status"
                error_expr = (
                    "error_message"
                    if "error_message" in columns
                    else "'' AS error_message"
                )
                rows = conn.execute(
                    f"""
                    SELECT id, run_at, {status_expr}, mode, model, {error_expr},
                           prompt_json, response_json, actions_json
                    FROM manager_runs
                    ORDER BY run_at DESC, id DESC
                    LIMIT 180
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read Binance manager_runs in {source_path}: {exc}"
            ) from exc

        grouped: dict[str, list[dict[str, Any]]] = {}

        def parsed(raw: str | None, *, field: str) -> dict[str, Any]:
            try:
                value = self._parse_json(
                    raw,
                    {},
                    field=field,
                    allow_missing=True,
                )
            except JueWikiDataIntegrityError as exc:
                raise JueWikiSourceReadError(str(exc)) from exc
            return value if isinstance(value, dict) else {}

        def list_items(value: Any, *, key: str = "") -> list[Any]:
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get(key) if key else value.get("items")
                return nested if isinstance(nested, list) else []
            return []

        def calculated(candidate: dict[str, Any]) -> dict[str, Any]:
            value = candidate.get("calculated")
            return value if isinstance(value, dict) else {}

        def add_observation(
            candidate: Any,
            *,
            run_id: str,
            run_at: str,
            mode: str,
            model: str,
            run_status: str,
            error_message: str,
            source_type: str,
            hold_decision: dict[str, Any],
            proactive_pressure: dict[str, Any],
            wiki_attention: dict[str, Any],
            memory_card_quality: dict[str, Any],
            wiki_evidence_quality: dict[str, Any],
        ) -> None:
            if not isinstance(candidate, dict):
                return
            symbol = _normalize_symbol(str(candidate.get("symbol") or ""))
            if not _is_crypto_tradable_symbol(symbol):
                return
            calc = calculated(candidate)
            crosscheck = (
                calc.get("pattern_live_crosscheck")
                if isinstance(calc.get("pattern_live_crosscheck"), dict)
                else {}
            )
            condition = str(candidate.get("condition") or "").strip()
            reason = str(
                candidate.get("reason")
                or candidate.get("reason_md")
                or candidate.get("summary")
                or ""
            ).strip()
            attention_item = (
                candidate.get("wiki_attention_item")
                if isinstance(candidate.get("wiki_attention_item"), dict)
                else {}
            )
            row = {
                "manager_run_id": run_id,
                "observed_at": run_at,
                "mode": mode,
                "model": model,
                "run_status": run_status,
                "error_message": error_message[:260],
                "symbol": symbol,
                "market": str(candidate.get("market") or "").strip(),
                "lane": str(candidate.get("lane") or calc.get("lane") or "").strip(),
                "side": str(candidate.get("side") or "").strip(),
                "horizon": str(candidate.get("horizon") or "").strip(),
                "source_type": source_type,
                "score": candidate.get("score"),
                "confidence": candidate.get("confidence"),
                "stance": str(candidate.get("stance") or "").strip(),
                "entry_style": str(candidate.get("entry_style") or "").strip(),
                "entry_operator": str(
                    candidate.get("entry_trigger_operator") or ""
                ).strip(),
                "entry_trigger_price": candidate.get("entry_trigger_price")
                if candidate.get("entry_trigger_price") is not None
                else candidate.get("price"),
                "entry_price": candidate.get("entry_price"),
                "target_price": candidate.get("target_price"),
                "stop_price": candidate.get("stop_price"),
                "reward_risk": calc.get("reward_risk")
                if calc.get("reward_risk") is not None
                else candidate.get("reward_risk"),
                "spread_bps": candidate.get("spread_bps"),
                "volatile_attack": calc.get("volatile_attack")
                if calc.get("volatile_attack") is not None
                else candidate.get("volatile_attack"),
                "crosscheck_status": crosscheck.get("status"),
                "crosscheck_mode": crosscheck.get("recommended_entry_mode"),
                "condition": condition,
                "reason": reason,
                "signals": candidate.get("signals")
                if isinstance(candidate.get("signals"), list)
                else [],
                "sources": candidate.get("sources")
                if isinstance(candidate.get("sources"), list)
                else self._manager_observation_sources(
                    candidate,
                    source_type=source_type,
                ),
                "hold_summary": str(hold_decision.get("summary") or "").strip(),
                "data_gaps": [
                    str(item).strip()
                    for item in list(hold_decision.get("data_gaps") or [])[:4]
                    if str(item).strip()
                ],
                "proactive_status": proactive_pressure.get("status"),
                "proactive_level": proactive_pressure.get("pressure_level"),
                "zero_action_streak": proactive_pressure.get("zero_action_streak"),
                "wiki_attention_status": wiki_attention.get("status"),
                "wiki_attention_resolution": wiki_attention.get("resolution_status"),
                "wiki_attention_component": attention_item.get("component")
                or wiki_attention.get("component"),
                "wiki_attention_action_type": attention_item.get("action_type")
                or wiki_attention.get("action_type"),
                "wiki_attention_recommended": attention_item.get(
                    "recommended_resolution"
                )
                or wiki_attention.get("recommended_resolution"),
                "wiki_attention_must_address": wiki_attention.get("must_address")
                if isinstance(wiki_attention.get("must_address"), list)
                else [],
                "wiki_attention_targets": attention_item.get("targets")
                if isinstance(attention_item.get("targets"), list)
                else wiki_attention.get("targets")
                if isinstance(wiki_attention.get("targets"), list)
                else [],
                "wiki_memory_card_quality_status": memory_card_quality.get("status"),
                "wiki_memory_card_quality_resolution": memory_card_quality.get(
                    "resolution_status"
                ),
                "wiki_memory_card_quality_required_action": memory_card_quality.get(
                    "required_action"
                ),
                "wiki_memory_card_quality_symbols": memory_card_quality.get(
                    "symbols"
                )
                if isinstance(memory_card_quality.get("symbols"), list)
                else [],
                "wiki_memory_card_quality_missing_fields": (
                    self._manager_wiki_memory_card_quality_missing_fields_for_symbol(
                        memory_card_quality,
                        symbol,
                    )
                ),
                "wiki_memory_card_quality_required_checks": memory_card_quality.get(
                    "required_checks"
                )
                if isinstance(memory_card_quality.get("required_checks"), list)
                else [],
                "wiki_evidence_quality_summary": wiki_evidence_quality.get(
                    "summary_line"
                ),
                "wiki_evidence_quality_status_counts": wiki_evidence_quality.get(
                    "status_counts"
                )
                if isinstance(wiki_evidence_quality.get("status_counts"), dict)
                else {},
                "wiki_evidence_quality_warnings": wiki_evidence_quality.get(
                    "top_warnings"
                )
                if isinstance(wiki_evidence_quality.get("top_warnings"), list)
                else [],
            }
            bucket = grouped.setdefault(symbol, [])
            key = (row["manager_run_id"], row["source_type"], row.get("condition"))
            if any(
                (
                    item.get("manager_run_id"),
                    item.get("source_type"),
                    item.get("condition"),
                )
                == key
                for item in bucket
            ):
                return
            bucket.append(row)

        for row in rows:
            run_id = str(row["id"] or "")
            run_at = str(row["run_at"] or "")
            run_status = str(row["status"] or "")
            error_message = str(row["error_message"] or "")
            mode = str(row["mode"] or "")
            model = str(row["model"] or "")
            prompt = parsed(row["prompt_json"], field=f"manager_runs.prompt_json:{run_id}")
            response = parsed(
                row["response_json"],
                field=f"manager_runs.response_json:{run_id}",
            )
            actions = parsed(row["actions_json"], field=f"manager_runs.actions_json:{run_id}")
            hold_decision = (
                response.get("hold_decision")
                if isinstance(response.get("hold_decision"), dict)
                else {}
            )
            latest_input_summary = (
                response.get("latest_input_summary")
                if isinstance(response.get("latest_input_summary"), dict)
                else {}
            )
            diagnostics = self._manager_prompt_diagnostics_summary(prompt)
            selection_observation = self._manager_wiki_selection_observation(prompt)
            wiki_attention = self._manager_wiki_attention_summary(
                latest_input_summary.get("jue_wiki_attention")
            ) or self._manager_wiki_attention_from_selection_observation(
                selection_observation,
                scope="binance",
            ) or self._manager_wiki_attention_from_diagnostics(
                diagnostics,
                scope="binance",
            )
            memory_card_quality = self._manager_wiki_memory_card_quality_summary(
                latest_input_summary.get("jue_wiki_memory_card_quality")
            ) or self._manager_wiki_memory_card_quality_from_diagnostics(
                diagnostics
            )
            wiki_evidence_quality = (
                self._manager_wiki_evidence_quality_from_selection_observation(
                    selection_observation
                )
            )
            proactive_pressure = (
                prompt.get("proactive_decision_pressure")
                if isinstance(prompt.get("proactive_decision_pressure"), dict)
                else {}
            )
            applied = (
                response.get("applied")
                if isinstance(response.get("applied"), dict)
                else {}
            )
            candidates = prompt.get("candidates")
            for candidate in list_items(candidates)[:16]:
                add_observation(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="manager_candidates",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list_items(proactive_pressure, key="top_candidates")[:12]:
                add_observation(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="proactive_decision_pressure",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list_items(hold_decision, key="next_triggers")[:12]:
                add_observation(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="hold_decision.next_trigger",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list_items(actions, key="create_blocks")[:12]:
                add_observation(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="manager_create_blocks",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for item in self._compact_items(applied.get("created"))[:12]:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "").strip().lower() != "rejected":
                    continue
                input_row = item.get("input")
                candidate = dict(input_row) if isinstance(input_row, dict) else {}
                candidate.setdefault("symbol", item.get("symbol"))
                candidate.setdefault("market", item.get("market"))
                candidate.setdefault("lane", item.get("lane"))
                candidate.setdefault("side", item.get("side"))
                candidate.setdefault("horizon", item.get("horizon"))
                candidate.setdefault("reason", item.get("reason"))
                candidate.setdefault("stance", "rejected")
                candidate.setdefault("condition", item.get("reason"))
                add_observation(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="manager_applied_created_rejected",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            attention_items = [
                item
                for item in list(wiki_attention.get("items") or [])[:6]
                if isinstance(item, dict)
            ]
            if not attention_items and wiki_attention.get("symbols"):
                attention_items = [wiki_attention]
            for attention_item in attention_items:
                for symbol in list(attention_item.get("symbols") or [])[:12]:
                    add_observation(
                        {
                            "symbol": symbol,
                            "stance": wiki_attention.get("resolution_status"),
                            "reason": attention_item.get("recommended_resolution"),
                            "condition": attention_item.get("recommended_resolution"),
                            "signals": ["jue_wiki_attention"],
                            "sources": [
                                wiki_attention.get("source")
                                or "latest_input_summary.jue_wiki_attention"
                            ],
                            "wiki_attention_item": attention_item,
                        },
                        run_id=run_id,
                        run_at=run_at,
                        mode=mode,
                        model=model,
                        run_status=run_status,
                        error_message=error_message,
                        source_type="jue_wiki_attention",
                        hold_decision=hold_decision,
                        proactive_pressure=proactive_pressure,
                        wiki_attention=wiki_attention,
                        memory_card_quality=memory_card_quality,
                        wiki_evidence_quality=wiki_evidence_quality,
                    )
            for symbol in list(memory_card_quality.get("symbols") or [])[:12]:
                add_observation(
                    {
                        "symbol": symbol,
                            "stance": memory_card_quality.get("resolution_status"),
                            "reason": memory_card_quality.get("required_action"),
                            "condition": memory_card_quality.get("required_action"),
                            "signals": ["jue_wiki_memory_card_quality"],
                            "sources": [
                                memory_card_quality.get("source")
                                or "latest_input_summary.jue_wiki_memory_card_quality"
                            ],
                        },
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="jue_wiki_memory_card_quality",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for symbol in list(hold_decision.get("watch_symbols") or [])[:16]:
                add_observation(
                    {"symbol": symbol},
                    run_id=run_id,
                    run_at=run_at,
                    mode=mode,
                    model=model,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="hold_decision.watch_symbol",
                    hold_decision=hold_decision,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
        return grouped

    def _deactivate_invalid_binance_symbol_pages(self) -> int:
        self.initialize()
        deactivated = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT page_id, symbols_json
                FROM wiki_pages
                WHERE scope = 'binance'
                  AND page_type = 'symbol'
                  AND status = 'active'
                """
            ).fetchall()
            for row in rows:
                symbols = self._parse_json(
                    row["symbols_json"],
                    [],
                    field=f"wiki_pages.symbols_json:{row['page_id']}",
                    allow_missing=True,
                )
                clean_symbols = [
                    _normalize_symbol(str(symbol))
                    for symbol in symbols
                    if str(symbol).strip()
                ]
                if clean_symbols and all(
                    _is_crypto_tradable_symbol(symbol) for symbol in clean_symbols
                ):
                    continue
                conn.execute(
                    """
                    UPDATE wiki_pages
                    SET status = 'inactive', updated_at = ?
                    WHERE page_id = ?
                    """,
                    (_utc_now_iso(), str(row["page_id"])),
                )
                deactivated += 1
        return deactivated

    def _crypto_research_lines_and_refs(
        self,
        *,
        symbol: str,
        research: dict[str, list[dict[str, Any]]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if not research:
            return [], []
        lines: list[str] = []
        refs: list[dict[str, Any]] = []
        for row in list(research.get("notes") or [])[:3]:
            updated_at = str(row.get("updated_at") or "").strip()
            reasons = ", ".join(
                str(item).strip()
                for item in list(row.get("reasons") or [])[:3]
                if str(item).strip()
            )
            risks = ", ".join(
                str(item).strip()
                for item in list(row.get("risks") or [])[:3]
                if str(item).strip()
            )
            triggers = ", ".join(
                str(item).strip()
                for item in list(row.get("triggers") or [])[:3]
                if str(item).strip()
            )
            lines.append(
                "- note stance={stance}, horizon={horizon}, confidence={confidence}, "
                "summary={summary}; reasons={reasons}; risks={risks}; triggers={triggers}".format(
                    stance=str(row.get("stance") or "").strip() or "-",
                    horizon=str(row.get("horizon") or "").strip() or "-",
                    confidence=row.get("confidence") or "",
                    summary=str(row.get("summary_md") or "").strip()[:280] or "-",
                    reasons=reasons or "-",
                    risks=risks or "-",
                    triggers=triggers or "-",
                )
            )
            refs.append(
                {
                    "source_type": "crypto_market_research",
                    "source_id": f"{symbol}:note",
                    "source_scope": "research",
                    "observed_at": updated_at,
                }
            )
        for row in list(research.get("candidates") or [])[:5]:
            template = (
                row.get("block_template")
                if isinstance(row.get("block_template"), dict)
                else {}
            )
            market = str(row.get("market") or "").strip()
            stance = str(row.get("stance") or "").strip()
            horizon = str(row.get("horizon") or "").strip()
            run_id = str(row.get("source_run_id") or "").strip()
            source_id = ":".join(
                part
                for part in [run_id, symbol, market, stance, horizon]
                if part
            )
            lines.append(
                "- candidate market={market}, stance={stance}, horizon={horizon}, "
                "score={score}, confidence={confidence}, entry={entry}, "
                "target={target}, stop={stop}, rr={rr}; reason={reason}".format(
                    market=market or "-",
                    stance=stance or "-",
                    horizon=horizon or "-",
                    score=row.get("score") or "",
                    confidence=row.get("confidence") or "",
                    entry=template.get("entry_price") or "-",
                    target=template.get("target_price") or "-",
                    stop=template.get("stop_price") or "-",
                    rr=template.get("reward_risk") or "-",
                    reason=str(row.get("reason_md") or "").strip()[:260] or "-",
                )
            )
            refs.append(
                {
                    "source_type": "crypto_candidates",
                    "source_id": source_id or f"{symbol}:candidate",
                    "source_scope": "research",
                    "observed_at": str(row.get("updated_at") or ""),
                }
            )
        return lines, refs

    def _read_kis_symbol_blocks(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.kis_blocks_db_path
        if path is None:
            return {}
        rows = self._read_rows_by_symbol(
            path=Path(path),
            table="blocks",
            columns={
                "block_id": ["block_id"],
                "symbol": ["symbol"],
                "name": ["name"],
                "status": ["status"],
                "qty_initial": ["qty_initial", "qty"],
                "entry_price": ["entry_price"],
                "target_price": ["target_price"],
                "stop_price": ["stop_price"],
                "thesis": ["thesis"],
                "llm_reason": ["llm_reason", "reason"],
                "risk_note": ["risk_note"],
                "created_at": ["created_at"],
                "closed_at": ["closed_at"],
                "realized_pnl": ["realized_pnl", "pnl_krw", "pnl"],
            },
            order_columns=["closed_at", "updated_at", "created_at"],
            limit=500,
        )
        return rows

    def _read_kis_manager_observations_by_symbol(
        self,
    ) -> dict[str, list[dict[str, Any]]]:
        path = self.config.kis_blocks_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "manager_runs"):
                    return {}
                columns = self._table_columns(conn, "manager_runs")
                status_expr = "status" if "status" in columns else "'' AS status"
                error_expr = (
                    "error_message"
                    if "error_message" in columns
                    else "'' AS error_message"
                )
                rows = conn.execute(
                    f"""
                    SELECT id, run_at, market_session, {status_expr}, {error_expr},
                           prompt_json, response_json, actions_json
                    FROM manager_runs
                    ORDER BY run_at DESC, id DESC
                    LIMIT 160
                    """
                ).fetchall()
                block_lookup = self._block_lookup_by_id(conn)
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read KIS manager_runs in {source_path}: {exc}"
            ) from exc

        grouped: dict[str, list[dict[str, Any]]] = {}

        def parsed(raw: str | None, *, field: str) -> dict[str, Any]:
            try:
                value = self._parse_json(
                    raw,
                    {},
                    field=field,
                    allow_missing=True,
                )
            except JueWikiDataIntegrityError as exc:
                raise JueWikiSourceReadError(str(exc)) from exc
            return value if isinstance(value, dict) else {}

        def add_candidate(
            candidate: Any,
            *,
            run_id: str,
            run_at: str,
            market_session: str,
            run_status: str,
            error_message: str,
            source_type: str,
            no_action_watch: dict[str, Any],
            proactive_pressure: dict[str, Any],
            wiki_attention: dict[str, Any],
            memory_card_quality: dict[str, Any],
            wiki_evidence_quality: dict[str, Any],
        ) -> None:
            if not isinstance(candidate, dict):
                return
            symbol = _normalize_symbol(str(candidate.get("symbol") or ""))
            if not re.fullmatch(r"\d{6}", symbol):
                return
            attention_item = (
                candidate.get("wiki_attention_item")
                if isinstance(candidate.get("wiki_attention_item"), dict)
                else {}
            )
            row = {
                "manager_run_id": run_id,
                "observed_at": run_at,
                "market_session": market_session,
                "run_status": run_status,
                "error_message": error_message[:260],
                "symbol": symbol,
                "name": str(candidate.get("name") or symbol),
                "source_type": source_type,
                "aggressive_score": candidate.get("aggressive_score")
                if candidate.get("aggressive_score") is not None
                else candidate.get("score"),
                "confidence": candidate.get("confidence"),
                "stance": candidate.get("stance"),
                "summary": str(candidate.get("summary") or "").strip(),
                "preferred_action": candidate.get("preferred_action"),
                "decision_use": candidate.get("decision_use"),
                "signals": candidate.get("signals")
                if isinstance(candidate.get("signals"), list)
                else self._manager_observation_signals(candidate),
                "sources": candidate.get("sources")
                if isinstance(candidate.get("sources"), list)
                else self._manager_observation_sources(candidate, source_type=source_type),
                "metrics": candidate.get("metrics")
                if isinstance(candidate.get("metrics"), dict)
                else self._manager_observation_metrics(candidate),
                "risks": candidate.get("risks")
                if isinstance(candidate.get("risks"), list)
                else [],
                "no_action_status": no_action_watch.get("status"),
                "no_action_reason": no_action_watch.get("reason"),
                "hold_summary": no_action_watch.get("hold_summary"),
                "proactive_status": proactive_pressure.get("status"),
                "proactive_level": proactive_pressure.get("pressure_level"),
                "zero_action_streak": proactive_pressure.get("zero_action_streak"),
                "required_resolution": str(
                    proactive_pressure.get("required_resolution") or ""
                ).strip(),
                "wiki_attention_status": wiki_attention.get("status"),
                "wiki_attention_resolution": wiki_attention.get("resolution_status"),
                "wiki_attention_component": attention_item.get("component")
                or wiki_attention.get("component"),
                "wiki_attention_action_type": attention_item.get("action_type")
                or wiki_attention.get("action_type"),
                "wiki_attention_recommended": attention_item.get(
                    "recommended_resolution"
                )
                or wiki_attention.get("recommended_resolution"),
                "wiki_attention_must_address": wiki_attention.get("must_address")
                if isinstance(wiki_attention.get("must_address"), list)
                else [],
                "wiki_attention_targets": attention_item.get("targets")
                if isinstance(attention_item.get("targets"), list)
                else wiki_attention.get("targets")
                if isinstance(wiki_attention.get("targets"), list)
                else [],
                "wiki_memory_card_quality_status": memory_card_quality.get("status"),
                "wiki_memory_card_quality_resolution": memory_card_quality.get(
                    "resolution_status"
                ),
                "wiki_memory_card_quality_required_action": memory_card_quality.get(
                    "required_action"
                ),
                "wiki_memory_card_quality_symbols": memory_card_quality.get(
                    "symbols"
                )
                if isinstance(memory_card_quality.get("symbols"), list)
                else [],
                "wiki_memory_card_quality_missing_fields": (
                    self._manager_wiki_memory_card_quality_missing_fields_for_symbol(
                        memory_card_quality,
                        symbol,
                    )
                ),
                "wiki_memory_card_quality_required_checks": memory_card_quality.get(
                    "required_checks"
                )
                if isinstance(memory_card_quality.get("required_checks"), list)
                else [],
                "wiki_evidence_quality_summary": wiki_evidence_quality.get(
                    "summary_line"
                ),
                "wiki_evidence_quality_status_counts": wiki_evidence_quality.get(
                    "status_counts"
                )
                if isinstance(wiki_evidence_quality.get("status_counts"), dict)
                else {},
                "wiki_evidence_quality_warnings": wiki_evidence_quality.get(
                    "top_warnings"
                )
                if isinstance(wiki_evidence_quality.get("top_warnings"), list)
                else [],
            }
            bucket = grouped.setdefault(symbol, [])
            key = (row["manager_run_id"], row["source_type"])
            if any((item.get("manager_run_id"), item.get("source_type")) == key for item in bucket):
                return
            bucket.append(row)

        for row in rows:
            run_id = str(row["id"] or "")
            run_at = str(row["run_at"] or "")
            market_session = str(row["market_session"] or "")
            run_status = str(row["status"] or "")
            error_message = str(row["error_message"] or "")
            prompt = parsed(row["prompt_json"], field=f"manager_runs.prompt_json:{run_id}")
            response = parsed(
                row["response_json"],
                field=f"manager_runs.response_json:{run_id}",
            )
            actions = parsed(row["actions_json"], field=f"manager_runs.actions_json:{run_id}")
            no_action_watch = (
                response.get("no_action_watch")
                if isinstance(response.get("no_action_watch"), dict)
                else {}
            )
            latest_input_summary = (
                response.get("latest_input_summary")
                if isinstance(response.get("latest_input_summary"), dict)
                else {}
            )
            diagnostics = self._manager_prompt_diagnostics_summary(prompt)
            selection_observation = self._manager_wiki_selection_observation(prompt)
            wiki_attention = self._manager_wiki_attention_summary(
                latest_input_summary.get("jue_wiki_attention")
            ) or self._manager_wiki_attention_from_selection_observation(
                selection_observation,
                scope="kis",
            ) or self._manager_wiki_attention_from_diagnostics(
                diagnostics,
                scope="kis",
            )
            memory_card_quality = self._manager_wiki_memory_card_quality_summary(
                latest_input_summary.get("jue_wiki_memory_card_quality")
            ) or self._manager_wiki_memory_card_quality_from_diagnostics(
                diagnostics
            )
            wiki_evidence_quality = (
                self._manager_wiki_evidence_quality_from_selection_observation(
                    selection_observation
                )
            )
            aggressive = (
                prompt.get("aggressive_opportunities")
                if isinstance(prompt.get("aggressive_opportunities"), dict)
                else {}
            )
            opportunity = (
                prompt.get("opportunity_research_brief")
                if isinstance(prompt.get("opportunity_research_brief"), dict)
                else {}
            )
            proactive_pressure = (
                prompt.get("proactive_decision_pressure")
                if isinstance(prompt.get("proactive_decision_pressure"), dict)
                else {}
            )
            for candidate in list(no_action_watch.get("top_candidates") or [])[:8]:
                add_candidate(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="no_action_watch",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list(proactive_pressure.get("top_candidates") or [])[:8]:
                add_candidate(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="proactive_decision_pressure",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list(latest_input_summary.get("aggressive_top") or [])[:8]:
                add_candidate(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="latest_input_summary",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list(aggressive.get("candidates") or [])[:8]:
                add_candidate(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="aggressive_opportunities",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for key, source_type in (
                ("pre_surge_candidates", "opportunity_research_brief.pre_surge"),
                ("block_candidates", "opportunity_research_brief.block_candidate"),
                (
                    "daily_discovery_candidates",
                    "opportunity_research_brief.daily_discovery",
                ),
                ("aggressive_candidates", "opportunity_research_brief.aggressive"),
            ):
                for candidate in list(opportunity.get(key) or [])[:8]:
                    add_candidate(
                        candidate,
                        run_id=run_id,
                        run_at=run_at,
                        market_session=market_session,
                        run_status=run_status,
                        error_message=error_message,
                        source_type=source_type,
                        no_action_watch=no_action_watch,
                        proactive_pressure=proactive_pressure,
                        wiki_attention=wiki_attention,
                        memory_card_quality=memory_card_quality,
                        wiki_evidence_quality=wiki_evidence_quality,
                    )
            attention_items = [
                item
                for item in list(wiki_attention.get("items") or [])[:6]
                if isinstance(item, dict)
            ]
            if not attention_items and wiki_attention.get("symbols"):
                attention_items = [wiki_attention]
            for attention_item in attention_items:
                for symbol in list(attention_item.get("symbols") or [])[:8]:
                    add_candidate(
                        {
                            "symbol": symbol,
                            "name": symbol,
                            "stance": wiki_attention.get("resolution_status"),
                            "summary": attention_item.get("recommended_resolution"),
                            "signals": ["jue_wiki_attention"],
                            "sources": [
                                wiki_attention.get("source")
                                or "latest_input_summary.jue_wiki_attention"
                            ],
                            "wiki_attention_item": attention_item,
                        },
                        run_id=run_id,
                        run_at=run_at,
                        market_session=market_session,
                        run_status=run_status,
                        error_message=error_message,
                        source_type="jue_wiki_attention",
                        no_action_watch=no_action_watch,
                        proactive_pressure=proactive_pressure,
                        wiki_attention=wiki_attention,
                        memory_card_quality=memory_card_quality,
                        wiki_evidence_quality=wiki_evidence_quality,
                    )
            for symbol in list(memory_card_quality.get("symbols") or [])[:8]:
                add_candidate(
                    {
                        "symbol": symbol,
                        "name": symbol,
                        "stance": memory_card_quality.get("resolution_status"),
                        "summary": memory_card_quality.get("required_action"),
                        "signals": ["jue_wiki_memory_card_quality"],
                        "sources": [
                            memory_card_quality.get("source")
                            or "latest_input_summary.jue_wiki_memory_card_quality"
                        ],
                    },
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="jue_wiki_memory_card_quality",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list(actions.get("create_blocks") or [])[:8]:
                add_candidate(
                    candidate,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="manager_create_blocks",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            for candidate in list(actions.get("close_blocks") or [])[:8]:
                if not isinstance(candidate, dict):
                    continue
                block_id = str(candidate.get("block_id") or "").strip()
                block = block_lookup.get(block_id, {})
                enriched = {
                    **candidate,
                    "symbol": candidate.get("symbol") or block.get("symbol"),
                    "name": candidate.get("name") or block.get("name"),
                    "stance": "close_requested",
                    "summary": candidate.get("reason") or candidate.get("summary"),
                    "signals": [
                        item
                        for item in (
                            candidate.get("close_trigger"),
                            candidate.get("decision_class"),
                        )
                        if item
                    ],
                    "sources": ["manager_close_blocks"],
                    "metrics": {
                        "block_id": block_id,
                        "target_price": block.get("target_price"),
                        "stop_price": block.get("stop_price"),
                    },
                }
                add_candidate(
                    enriched,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="manager_close_blocks",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
            applied = (
                actions.get("_applied")
                if isinstance(actions.get("_applied"), dict)
                else response.get("applied")
                if isinstance(response.get("applied"), dict)
                else {}
            )
            for item in self._compact_items(applied.get("rejected"))[:12]:
                if not isinstance(item, dict):
                    continue
                row = item.get("row") if isinstance(item.get("row"), dict) else {}
                block_id = str(item.get("block_id") or row.get("block_id") or "").strip()
                block = block_lookup.get(block_id, {})
                enriched = {
                    **row,
                    "symbol": item.get("symbol") or row.get("symbol") or block.get("symbol"),
                    "name": item.get("name") or row.get("name") or block.get("name"),
                    "stance": "rejected",
                    "summary": item.get("reason") or row.get("reason"),
                    "preferred_action": item.get("action") or row.get("action"),
                    "signals": [
                        str(value)
                        for value in (
                            item.get("reason"),
                            item.get("horizon"),
                            item.get("action"),
                        )
                        if str(value or "").strip()
                    ],
                    "sources": ["manager_applied_rejected"],
                    "metrics": {
                        "block_id": block_id,
                        "target_price": item.get("target_price")
                        or row.get("target_price")
                        or block.get("target_price"),
                        "stop_price": item.get("stop_price")
                        or row.get("stop_price")
                        or block.get("stop_price"),
                    },
                }
                add_candidate(
                    enriched,
                    run_id=run_id,
                    run_at=run_at,
                    market_session=market_session,
                    run_status=run_status,
                    error_message=error_message,
                    source_type="manager_applied_rejected",
                    no_action_watch=no_action_watch,
                    proactive_pressure=proactive_pressure,
                    wiki_attention=wiki_attention,
                    memory_card_quality=memory_card_quality,
                    wiki_evidence_quality=wiki_evidence_quality,
                )
        return grouped

    @classmethod
    def _manager_prompt_diagnostics_summary(
        cls,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(prompt, dict):
            return {}
        direct = prompt.get("diagnostics")
        if isinstance(direct, dict) and direct:
            return direct
        context = (
            prompt.get("compact_manager_context")
            if isinstance(prompt.get("compact_manager_context"), dict)
            else {}
        )
        diagnostics = context.get("diagnostics") if isinstance(context, dict) else {}
        return diagnostics if isinstance(diagnostics, dict) else {}

    @classmethod
    def _manager_wiki_selection_observation(
        cls,
        prompt: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(prompt, dict):
            return {}
        direct = prompt.get("jue_wiki_selection_observation")
        if isinstance(direct, dict) and direct:
            return direct
        context = (
            prompt.get("compact_manager_context")
            if isinstance(prompt.get("compact_manager_context"), dict)
            else {}
        )
        nested = context.get("jue_wiki_selection_observation")
        return nested if isinstance(nested, dict) else {}

    @classmethod
    def _manager_wiki_attention_from_selection_observation(
        cls,
        observation: dict[str, Any],
        *,
        scope: str,
    ) -> dict[str, Any]:
        if not isinstance(observation, dict) or not observation:
            return {}
        clean_scope = _normalize_scope(scope)
        items: list[dict[str, Any]] = []
        all_symbols: list[str] = []
        all_targets: list[str] = []
        for batch in list(observation.get("repair_action_batches") or [])[:8]:
            if not isinstance(batch, dict):
                continue
            action_type = str(batch.get("action_type") or "").strip()
            if not action_type:
                continue
            symbols: list[str] = []
            for raw_symbol in list(batch.get("symbols") or [])[:24]:
                symbol = _normalize_symbol(str(raw_symbol or ""))
                if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(
                    symbol
                ):
                    symbols.append(symbol)
            symbols = list(dict.fromkeys(symbols))[:12]
            targets = [f"{clean_scope}.symbol.{symbol}" for symbol in symbols]
            all_symbols.extend(symbols)
            all_targets.extend(targets)
            items.append(
                {
                    "component": "manager_prompt_selection_observation",
                    "action_type": action_type,
                    "recommended_resolution": action_type,
                    "symbols": symbols,
                    "targets": targets,
                }
            )
        if not items:
            return {}
        first = items[0]
        return {
            "status": "active",
            "resolution_status": "unresolved",
            "must_address": list(
                dict.fromkeys(
                    str(item.get("action_type") or "")
                    for item in items
                    if item.get("action_type")
                )
            )[:6],
            "source": "prompt.jue_wiki_selection_observation",
            "component": first.get("component"),
            "action_type": first.get("action_type"),
            "recommended_resolution": first.get("recommended_resolution"),
            "symbols": list(dict.fromkeys(all_symbols))[:12],
            "targets": list(dict.fromkeys(all_targets))[:12],
            "items": items,
        }

    @classmethod
    def _manager_wiki_evidence_quality_from_selection_observation(
        cls,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(observation, dict) or not observation:
            return {}
        quality = observation.get("evidence_quality")
        if not isinstance(quality, dict):
            return {}
        out: dict[str, Any] = {
            "source": "prompt.jue_wiki_selection_observation",
        }
        summary_line = str(quality.get("summary_line") or "").strip()
        if summary_line:
            out["summary_line"] = summary_line[:260]
        status_counts = quality.get("status_counts")
        if isinstance(status_counts, dict) and status_counts:
            counts: dict[str, int] = {}
            for raw_key, raw_value in status_counts.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                count = int(cls._manager_number(raw_value) or 0)
                if count > 0:
                    counts[key] = counts.get(key, 0) + count
            if counts:
                out["status_counts"] = counts
        top_warnings = [
            str(item).strip()[:160]
            for item in list(quality.get("top_warnings") or [])[:8]
            if str(item).strip()
        ]
        if top_warnings:
            out["top_warnings"] = list(dict.fromkeys(top_warnings))
        return {key: value for key, value in out.items() if value not in ("", [], {})}

    @classmethod
    def _manager_diagnostics_symbols(cls, diagnostics: dict[str, Any]) -> list[str]:
        if not isinstance(diagnostics, dict):
            return []
        symbols: list[str] = []
        for key in (
            "jue_wiki_missing_summary_symbols",
            "jue_wiki_prompt_omitted_symbols",
            "jue_wiki_weak_memory_card_symbols",
        ):
            for item in list(diagnostics.get(key) or [])[:24]:
                symbol = _normalize_symbol(str(item or ""))
                if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(
                    symbol
                ):
                    symbols.append(symbol)
        return list(dict.fromkeys(symbols))[:24]

    @classmethod
    def _manager_wiki_attention_from_diagnostics(
        cls,
        diagnostics: dict[str, Any],
        *,
        scope: str,
    ) -> dict[str, Any]:
        if not isinstance(diagnostics, dict) or not diagnostics:
            return {}
        blocker_tags = (
            diagnostics.get("blocker_tags")
            if isinstance(diagnostics.get("blocker_tags"), dict)
            else {}
        )
        active_blockers = [
            str(key)
            for key, value in blocker_tags.items()
            if _metric_value_is_present(value) and cls._manager_number(value) > 0
        ][:8]
        symbols = cls._manager_diagnostics_symbols(diagnostics)
        if not active_blockers and not symbols:
            return {}
        must_address = [
            str(item).strip()[:160]
            for item in list(diagnostics.get("jue_wiki_attention_must_address") or [])[:6]
            if str(item).strip()
        ] or active_blockers
        action_type = (
            "collect_or_rebuild_requested_symbol_wiki_summary"
            if "unresolved_jue_wiki_requested_symbol_coverage" in active_blockers
            else active_blockers[0]
            if active_blockers
            else "review_manager_diagnostics"
        )
        repair_targets = [
            f"{_normalize_scope(scope)}.symbol.{symbol}"
            for symbol in symbols[:12]
        ]
        summary = {
            "status": "active",
            "resolution_status": "unresolved",
            "must_address": must_address,
            "source": "prompt.diagnostics",
            "component": "manager_prompt_diagnostics",
            "action_type": action_type,
            "recommended_resolution": action_type,
            "symbols": symbols,
            "targets": repair_targets,
            "items": [
                {
                    "component": "manager_prompt_diagnostics",
                    "action_type": blocker,
                    "recommended_resolution": action_type,
                    "symbols": symbols,
                    "targets": repair_targets,
                }
                for blocker in active_blockers[:4]
            ],
        }
        if not summary["items"]:
            summary["items"] = [
                {
                    "component": "manager_prompt_diagnostics",
                    "action_type": action_type,
                    "recommended_resolution": action_type,
                    "symbols": symbols,
                    "targets": repair_targets,
                }
            ]
        return {key: value for key, value in summary.items() if value not in ("", [], {})}

    @classmethod
    def _manager_wiki_memory_card_quality_from_diagnostics(
        cls,
        diagnostics: dict[str, Any],
    ) -> dict[str, Any]:
        symbols = cls._manager_diagnostics_symbols(diagnostics)
        if not symbols:
            return {}
        blocker_tags = (
            diagnostics.get("blocker_tags")
            if isinstance(diagnostics.get("blocker_tags"), dict)
            else {}
        )
        if not (
            blocker_tags.get("unresolved_jue_wiki_requested_symbol_coverage")
            or blocker_tags.get("unresolved_jue_wiki_memory_card_quality")
            or diagnostics.get("jue_wiki_missing_summary_symbols")
            or diagnostics.get("jue_wiki_weak_memory_card_symbols")
        ):
            return {}
        missing_fields_by_symbol = [
            {
                "symbol": symbol,
                "status": "weak",
                "missing_fields": [
                    "summary",
                    "durable_facts",
                    "evidence_links",
                ],
            }
            for symbol in symbols[:12]
        ]
        return {
            "status": "active",
            "resolution_status": "unresolved",
            "required_action": "collect_or_rebuild_requested_symbol_wiki_summary",
            "source": "prompt.diagnostics",
            "symbols": symbols[:12],
            "missing_fields_by_symbol": missing_fields_by_symbol,
            "required_checks": [
                "refresh_symbol_reports_fundamentals_and_manager_context",
                "record_missing_summary_resolution_in_next_manager_run",
            ],
        }

    def _block_lookup_by_id(
        self,
        conn: sqlite3.Connection,
    ) -> dict[str, dict[str, Any]]:
        if not self._table_exists(conn, "blocks"):
            return {}
        columns = self._table_columns(conn, "blocks")
        if not {"block_id", "symbol"} <= columns:
            return {}
        select_exprs = [
            self._select_expr(
                table_columns=columns,
                aliases=aliases,
                output_name=output_name,
            )
            for output_name, aliases in {
                "block_id": ["block_id"],
                "symbol": ["symbol"],
                "name": ["name"],
                "target_price": ["target_price"],
                "stop_price": ["stop_price"],
            }.items()
        ]
        rows = conn.execute(
            f"""
            SELECT {", ".join(select_exprs)}
            FROM blocks
            WHERE block_id IS NOT NULL AND TRIM(block_id) != ''
            """
        ).fetchall()
        return {
            str(row["block_id"]): {key: row[key] for key in row.keys()}
            for row in rows
            if str(row["block_id"] or "").strip()
        }

    @staticmethod
    def _compact_items(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            items = value.get("items")
            return items if isinstance(items, list) else []
        return []

    @classmethod
    def _manager_wiki_attention_summary(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def compact_list(raw: Any, *, limit: int = 8) -> list[str]:
            if not isinstance(raw, list):
                return []
            items: list[str] = []
            for item in raw:
                text = str(item or "").strip()
                if text:
                    items.append(text[:160])
                if len(items) >= limit:
                    break
            return items

        def compact_attention_item(source: Any) -> dict[str, Any]:
            if not isinstance(source, dict):
                return {}
            symbols: list[str] = []
            targets: list[str] = []
            for key in ("impacted_symbols", "symbols", "watch_symbols"):
                for item in compact_list(source.get(key)):
                    symbol = _normalize_symbol(item)
                    if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(
                        symbol
                    ):
                        symbols.append(symbol)
            for key in ("repair_targets", "impacted_page_ids", "page_ids"):
                for item in compact_list(source.get(key)):
                    targets.append(item)
                    match = re.search(r"(?:kis|binance)\.symbol\.([A-Z0-9]{3,20})", item)
                    if match:
                        symbol = _normalize_symbol(match.group(1))
                        if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(
                            symbol
                        ):
                            symbols.append(symbol)
            compact = {
                "component": str(source.get("component") or "").strip(),
                "action_type": str(source.get("action_type") or "").strip(),
                "recommended_resolution": str(
                    source.get("recommended_resolution") or ""
                ).strip()[:260],
                "symbols": list(dict.fromkeys(symbols))[:12],
                "targets": list(dict.fromkeys(targets))[:12],
            }
            return {
                key: item
                for key, item in compact.items()
                if item not in ("", [], {}, None)
            }

        repair = compact_attention_item(value.get("repair_now"))
        probe = compact_attention_item(value.get("probe_next"))
        additional = (
            [
                item
                for item in (
                    compact_attention_item(row)
                    for row in value.get("additional_attention", [])[:4]
                )
                if item
            ]
            if isinstance(value.get("additional_attention"), list)
            else []
        )
        primary = repair or probe or (additional[0] if additional else {})
        attention_items = [
            item for item in [repair, probe, *additional] if item
        ][:6]

        symbols: list[str] = []
        targets: list[str] = []
        for source in (value, *attention_items):
            for key in ("impacted_symbols", "symbols", "watch_symbols"):
                for item in compact_list(source.get(key)):
                    symbol = _normalize_symbol(item)
                    if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(symbol):
                        symbols.append(symbol)
            for key in ("repair_targets", "impacted_page_ids", "page_ids"):
                for item in compact_list(source.get(key)):
                    targets.append(item)
                    match = re.search(r"(?:kis|binance)\.symbol\.([A-Z0-9]{3,20})", item)
                    if match:
                        symbol = _normalize_symbol(match.group(1))
                        if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(symbol):
                            symbols.append(symbol)

        return {
            "status": str(value.get("status") or "").strip(),
            "resolution_status": str(value.get("resolution_status") or "").strip(),
            "must_address": compact_list(value.get("must_address"), limit=6),
            "component": str(primary.get("component") or "").strip(),
            "action_type": str(primary.get("action_type") or "").strip(),
            "recommended_resolution": str(
                primary.get("recommended_resolution") or ""
            ).strip()[:260],
            "symbols": list(dict.fromkeys(symbols))[:12],
            "targets": list(dict.fromkeys(targets))[:12],
            "items": attention_items,
        }

    @classmethod
    def _manager_wiki_memory_card_quality_summary(
        cls,
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def compact_list(raw: Any, *, limit: int = 12) -> list[str]:
            if not isinstance(raw, list):
                return []
            items: list[str] = []
            for item in raw:
                text = str(item or "").strip()
                if text:
                    items.append(text[:160])
                if len(items) >= limit:
                    break
            return items

        symbols: list[str] = []
        for key in ("weak_symbols", "symbols", "watch_symbols"):
            for item in compact_list(value.get(key)):
                symbol = _normalize_symbol(item)
                if re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(
                    symbol
                ):
                    symbols.append(symbol)
        missing_fields_by_symbol: list[dict[str, Any]] = []
        for row in list(value.get("missing_fields_by_symbol") or [])[:12]:
            if not isinstance(row, dict):
                continue
            symbol = _normalize_symbol(str(row.get("symbol") or ""))
            if not (
                re.fullmatch(r"\d{6}", symbol) or _is_crypto_tradable_symbol(symbol)
            ):
                continue
            missing_fields = [
                str(item).strip()[:80]
                for item in list(row.get("missing_fields") or [])[:8]
                if str(item).strip()
            ]
            if not missing_fields:
                continue
            missing_fields_by_symbol.append(
                {
                    "symbol": symbol,
                    "status": str(row.get("status") or "").strip()[:80],
                    "missing_fields": missing_fields,
                }
            )

        return {
            "status": str(value.get("status") or "").strip(),
            "resolution_status": str(value.get("resolution_status") or "").strip(),
            "required_action": str(value.get("required_action") or "").strip()[:260],
            "symbols": list(dict.fromkeys(symbols))[:12],
            "missing_fields_by_symbol": missing_fields_by_symbol,
            "required_checks": compact_list(value.get("required_checks"), limit=8),
        }

    @classmethod
    def _manager_wiki_memory_card_quality_missing_fields_for_symbol(
        cls,
        memory_card_quality: dict[str, Any],
        symbol: str,
    ) -> list[str]:
        normalized = _normalize_symbol(str(symbol or ""))
        for row in list(memory_card_quality.get("missing_fields_by_symbol") or [])[:12]:
            if not isinstance(row, dict):
                continue
            if _normalize_symbol(str(row.get("symbol") or "")) != normalized:
                continue
            return [
                str(item).strip()
                for item in list(row.get("missing_fields") or [])[:8]
                if str(item).strip()
            ]
        return []

    @staticmethod
    def _manager_observation_signals(candidate: dict[str, Any]) -> list[str]:
        signals: list[str] = []
        pre_surge = (
            candidate.get("pre_surge")
            if isinstance(candidate.get("pre_surge"), dict)
            else {}
        )
        if pre_surge.get("is_candidate"):
            signals.append("pre_surge")
        for key in ("reasons", "checks"):
            for value in list(candidate.get(key) or [])[:3]:
                text = str(value).strip()
                if text:
                    signals.append(text[:120])
        return signals[:6]

    @staticmethod
    def _manager_observation_sources(
        candidate: dict[str, Any],
        *,
        source_type: str,
    ) -> list[str]:
        sources = [source_type]
        source = str(candidate.get("source") or "").strip()
        bucket = str(candidate.get("bucket") or "").strip()
        if source:
            sources.append(source)
        if bucket:
            sources.append(bucket)
        return list(dict.fromkeys(sources))[:6]

    @staticmethod
    def _manager_observation_metrics(candidate: dict[str, Any]) -> dict[str, Any]:
        pre_surge = (
            candidate.get("pre_surge")
            if isinstance(candidate.get("pre_surge"), dict)
            else {}
        )
        metrics = {
            "score": candidate.get("score"),
            "confidence": candidate.get("confidence"),
            "decision_use": candidate.get("decision_use"),
            "pre_surge_score": pre_surge.get("score"),
            "pre_surge_lane": pre_surge.get("lane"),
            "entry_bias": pre_surge.get("entry_bias"),
            "preferred_horizon": pre_surge.get("preferred_horizon"),
        }
        return {key: value for key, value in metrics.items() if value not in ("", None)}

    def _read_daily_discovery_by_symbol(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.daily_discovery_db_path
        if path is None:
            return {}
        source_path = Path(path)
        if not source_path.exists():
            return {}
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "discovery_runs"):
                    return {}
                rows = conn.execute(
                    """
                    SELECT trading_day, status, results_json, summary_json, updated_at
                    FROM discovery_runs
                    ORDER BY trading_day DESC, id DESC
                    LIMIT 90
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read daily discovery DB in {source_path}: {exc}"
            ) from exc

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            trading_day = str(row["trading_day"] or "")
            try:
                results = self._parse_json(
                    row["results_json"],
                    [],
                    field=f"discovery_runs.results_json:{trading_day}",
                    allow_missing=True,
                )
                summary = self._parse_json(
                    row["summary_json"],
                    {},
                    field=f"discovery_runs.summary_json:{trading_day}",
                    allow_missing=True,
                )
            except JueWikiDataIntegrityError as exc:
                raise JueWikiSourceReadError(str(exc)) from exc
            if not isinstance(results, list):
                continue
            summary_payload = summary if isinstance(summary, dict) else {}
            for item in results:
                if not isinstance(item, dict):
                    continue
                item = enrich_discovery_result(item)
                symbol = _normalize_symbol(str(item.get("symbol") or ""))
                if not re.fullmatch(r"\d{6}", symbol):
                    continue
                analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else {}
                pre_surge = (
                    item.get("pre_surge")
                    if isinstance(item.get("pre_surge"), dict)
                    else {}
                )
                note = {
                    "trading_day": trading_day,
                    "observed_at": str(row["updated_at"] or ""),
                    "run_status": str(row["status"] or ""),
                    "symbol": symbol,
                    "name": str(item.get("name") or analysis.get("name") or symbol),
                    "market": str(item.get("market") or ""),
                    "status": str(item.get("status") or ""),
                    "score": item.get("score"),
                    "stance": analysis.get("stance"),
                    "confidence": analysis.get("confidence"),
                    "summary": analysis.get("summary"),
                    "reasons": analysis.get("reasons")
                    if isinstance(analysis.get("reasons"), list)
                    else [],
                    "risks": analysis.get("risks")
                    if isinstance(analysis.get("risks"), list)
                    else [],
                    "pre_surge": pre_surge,
                    "run_summary": summary_payload,
                }
                grouped.setdefault(symbol, []).append(note)
        return grouped

    def _read_latest_trading_validation(
        self,
        *,
        scope: str,
    ) -> dict[str, Any] | None:
        path = self.config.trading_validation_db_path
        if path is None:
            return None
        source_path = Path(path)
        if not source_path.exists():
            return None
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "validation_runs"):
                    return None
                table_columns = self._table_columns(conn, "validation_runs")
                if not {"venue", "payload_json", "computed_at"} <= table_columns:
                    raise JueWikiSourceReadError(
                        f"validation_runs in {source_path} is missing required columns"
                    )
                row = conn.execute(
                    """
                    SELECT *
                    FROM validation_runs
                    WHERE venue = ?
                    ORDER BY computed_at DESC
                    LIMIT 1
                    """,
                    (scope,),
                ).fetchone()
        except JueWikiSourceReadError:
            raise
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read trading validation DB in {source_path}: {exc}"
            ) from exc
        if row is None:
            return None
        payload = self._parse_json(
            row["payload_json"],
            {},
            field=f"validation_runs.payload_json:{row['run_id']}",
            allow_missing=True,
        )
        if not isinstance(payload, dict):
            payload = {}
        return {
            "run_id": str(row["run_id"] or payload.get("run_id") or ""),
            "venue": str(row["venue"] or scope),
            "scope": str(row["scope"] or payload.get("scope") or "live"),
            "status": str(row["status"] or payload.get("status") or ""),
            "strategy_revision_id": str(
                row["strategy_revision_id"]
                if "strategy_revision_id" in row.keys()
                else payload.get("strategy_revision_id")
                or ""
            ),
            "computed_at": str(row["computed_at"] or payload.get("computed_at") or ""),
            "total_score": row["total_score"] if "total_score" in row.keys() else "",
            "pass_count": row["pass_count"] if "pass_count" in row.keys() else "",
            "warn_count": row["warn_count"] if "warn_count" in row.keys() else "",
            "fail_count": row["fail_count"] if "fail_count" in row.keys() else "",
            "missing_count": row["missing_count"]
            if "missing_count" in row.keys()
            else "",
            "payload": payload,
        }

    def _rebuild_trading_validation_risk_page(self, *, scope: str) -> int:
        validation = self._read_latest_trading_validation(scope=scope)
        if not validation:
            return 0
        run_id = str(validation.get("run_id") or "").strip()
        source_refs = [
            {
                "source_type": "trading_validation",
                "source_id": run_id or f"{scope}:latest",
                "source_scope": scope,
                "observed_at": str(validation.get("computed_at") or ""),
            }
        ]
        payload = (
            validation.get("payload")
            if isinstance(validation.get("payload"), dict)
            else {}
        )
        for row in list(payload.get("disciplines") or [])[:19]:
            if not isinstance(row, dict):
                continue
            discipline_id = str(row.get("id") or "").strip()
            status = str(row.get("status") or "").strip().lower()
            if not discipline_id or status not in {"fail", "warn"}:
                continue
            source_refs.append(
                {
                    "source_type": "trading_validation_discipline",
                    "source_id": f"{run_id}:{discipline_id}" if run_id else discipline_id,
                    "source_scope": scope,
                    "observed_at": str(validation.get("computed_at") or ""),
                }
            )
        self.write_page(
            scope=scope,
            page_type="risk",
            key="trading_validation",
            title=f"{scope.upper()} Trading Validation Risk",
            symbols=[],
            content_sections=self._build_trading_validation_risk_sections(
                scope=scope,
                validation=validation,
            ),
            source_refs=source_refs,
            confidence=0.86,
            freshness="fresh",
        )
        return 1

    def _read_codex_lab_green_path_progress(
        self,
        *,
        scope: str,
    ) -> list[dict[str, Any]]:
        path = self.config.jue_codex_lab_db_path
        if path is None:
            return []
        source_path = Path(path)
        if not source_path.exists():
            return []
        try:
            with sqlite3.connect(source_path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, "green_path_progress"):
                    return []
                columns = self._table_columns(conn, "green_path_progress")
                clean_scope = _normalize_scope(scope)
                order_terms: list[str] = []
                if "created_at" in columns:
                    order_terms.append("created_at DESC")
                if "progress_id" in columns:
                    order_terms.append("progress_id DESC")
                order_terms.append("rowid DESC")
                order_sql = ", ".join(order_terms)
                if "venue" in columns:
                    rows = conn.execute(
                        f"""
                        SELECT *
                        FROM green_path_progress
                        WHERE LOWER(COALESCE(venue, '')) = ?
                        ORDER BY {order_sql}
                        LIMIT 50
                        """,
                        (clean_scope,),
                    ).fetchall()
                elif "progress_json" in columns:
                    rows = conn.execute(
                        f"""
                        SELECT *
                        FROM green_path_progress
                        WHERE json_valid(progress_json)
                          AND LOWER(
                            COALESCE(json_extract(progress_json, '$.venue'), '')
                          ) = ?
                        ORDER BY {order_sql}
                        LIMIT 50
                        """,
                        (clean_scope,),
                    ).fetchall()
                else:
                    return []
        except JueWikiSourceReadError as exc:
            raise JueWikiSourceReadError(
                f"failed to read Codex Lab green path DB in {source_path}: {exc}"
            ) from exc
        except sqlite3.DatabaseError:
            raise JueWikiSourceReadError(
                f"failed to read Codex Lab green path DB in {source_path}"
            )
        clean_scope = _normalize_scope(scope)
        progress_rows: list[dict[str, Any]] = []
        for row in rows:
            row_dict = dict(row)
            progress: dict[str, Any] = {}
            if "progress_json" in columns:
                raw_progress = row_dict.get("progress_json")
                if raw_progress not in (None, ""):
                    try:
                        decoded = json.loads(str(raw_progress))
                    except (TypeError, json.JSONDecodeError):
                        decoded = {}
                    if isinstance(decoded, dict):
                        progress = decoded
            merged = {**row_dict, **progress}
            venue = str(merged.get("venue") or "").strip().lower()
            if venue != clean_scope:
                continue
            progress_rows.append(
                {
                    "progress_id": str(merged.get("progress_id") or ""),
                    "venue": venue,
                    "discipline_id": str(
                        merged.get("discipline_id") or ""
                    ).strip(),
                    "before_status": str(
                        merged.get("before_status") or ""
                    ).strip().lower(),
                    "after_status": str(
                        merged.get("after_status") or merged.get("status") or ""
                    ).strip().lower(),
                    "before_score": merged.get("before_score"),
                    "after_score": merged.get("after_score"),
                    "validation_run_before": str(
                        merged.get("validation_run_before") or ""
                    ).strip(),
                    "validation_run_after": str(
                        merged.get("validation_run_after") or ""
                    ).strip(),
                    "repair_task_id": str(
                        merged.get("repair_task_id") or merged.get("task_id") or ""
                    ).strip(),
                    "created_at": str(merged.get("created_at") or "").strip(),
                }
            )
        return progress_rows

    def _rebuild_codex_lab_green_path_page(self, *, scope: str) -> int:
        rows = self._read_codex_lab_green_path_progress(scope=scope)
        if not rows:
            self._remove_page(
                scope=scope,
                page_type="ops",
                key="codex_lab_green_path",
            )
            return 0
        transition_lines: list[str] = []
        repair_lines: list[str] = []
        source_refs: list[dict[str, Any]] = []
        for row in rows[:24]:
            discipline_id = row.get("discipline_id") or "unknown"
            before_status = row.get("before_status") or "unknown"
            after_status = row.get("after_status") or "unknown"
            before_score = self._format_green_path_score(row.get("before_score"))
            after_score = self._format_green_path_score(row.get("after_score"))
            repair_task_id = row.get("repair_task_id") or "-"
            validation_run_before = row.get("validation_run_before") or "-"
            validation_run_after = row.get("validation_run_after") or "-"
            transition_lines.append(
                f"- {discipline_id}: {before_status} -> {after_status}; "
                f"score={before_score} -> {after_score}"
            )
            repair_lines.append(
                f"- {discipline_id}: repair_task_id={repair_task_id}; "
                f"validation_run_before={validation_run_before}; "
                f"validation_run_after={validation_run_after}"
            )
            source_refs.append(
                {
                    "source_type": "codex_lab_green_path_progress",
                    "source_id": str(row.get("progress_id") or repair_task_id),
                    "source_scope": scope,
                    "observed_at": str(row.get("created_at") or ""),
                }
            )
        latest = rows[0]
        self.write_page(
            scope=scope,
            page_type="ops",
            key="codex_lab_green_path",
            title=f"{scope.upper()} Codex Lab Green Path",
            symbols=[],
            content_sections={
                "Current Stance": (
                    f"{scope} Codex Lab green-path progress is active repair memory "
                    "for turning validation failures into weaker warnings or passes."
                ),
                "Durable Facts": "\n".join(transition_lines),
                "Evidence Links": "\n".join(
                    f"- codex_lab_green_path_progress:{ref['source_id']}"
                    for ref in source_refs
                ),
                "Lessons": (
                    "- Green-path movement should be preserved with the exact "
                    "discipline, score delta, repair task, and validation run ids."
                ),
                "Open Questions": (
                    "- Did the next trading validation run keep the improved status?\n"
                    "- Which repair tasks should be promoted into durable strategy memory?"
                ),
                "Next Context Pack Summary": (
                    f"{scope} latest Codex Lab green path: "
                    f"{latest.get('discipline_id') or 'unknown'} "
                    f"{latest.get('before_status') or 'unknown'} -> "
                    f"{latest.get('after_status') or 'unknown'}."
                ),
                "Action Pressure": "\n".join(transition_lines),
                "Resolution Queue": "\n".join(repair_lines),
                "Probe Mandate": (
                    "- Use improved disciplines as permission to probe only where "
                    "the score delta is backed by a validation run id."
                ),
            },
            source_refs=source_refs,
            confidence=0.84,
            freshness="fresh",
        )
        return 1

    def _remove_page(self, *, scope: str, page_type: str, key: str) -> None:
        page_id = self.page_id(scope=scope, page_type=page_type, key=key)
        path = self.page_path(page_id=page_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        with self._connect() as conn:
            if not self._table_exists(conn, "wiki_pages"):
                return
            if self._table_exists(conn, "wiki_source_refs"):
                conn.execute("DELETE FROM wiki_source_refs WHERE page_id = ?", (page_id,))
            conn.execute(
                """
                UPDATE wiki_pages
                SET status = 'inactive',
                    source_refs_json = '[]',
                    updated_at = ?
                WHERE page_id = ?
                """,
                (_utc_now_iso(), page_id),
            )

    @staticmethod
    def _format_green_path_score(value: Any) -> str:
        if value in (None, ""):
            return "-"
        if isinstance(value, (int, float)):
            return f"{float(value):.4g}"
        return str(value).strip() or "-"

    def _build_trading_validation_risk_sections(
        self,
        *,
        scope: str,
        validation: dict[str, Any],
    ) -> dict[str, str]:
        payload = (
            validation.get("payload")
            if isinstance(validation.get("payload"), dict)
            else {}
        )
        summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        metrics = (
            payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        )
        remediation = (
            payload.get("remediation_plan")
            if isinstance(payload.get("remediation_plan"), dict)
            else {}
        )
        lane_authority_summary = (
            payload.get("lane_authority_summary")
            if isinstance(payload.get("lane_authority_summary"), dict)
            else metrics.get("lane_authority_summary")
            if isinstance(metrics.get("lane_authority_summary"), dict)
            else {}
        )
        if not lane_authority_summary and isinstance(
            metrics.get("lane_scorecards"),
            dict,
        ):
            lane_authority_summary = self._derive_lane_authority_summary(
                metrics["lane_scorecards"],
                allow_scale_up=bool(summary.get("scale_up_allowed")),
            )
        disciplines = [
            row
            for row in list(payload.get("disciplines") or [])
            if isinstance(row, dict)
        ]
        failing_or_warning = [
            row
            for row in disciplines
            if str(row.get("status") or "").strip().lower() in {"fail", "warn"}
        ]
        failed_lines: list[str] = []
        for row in failing_or_warning[:12]:
            failed_lines.append(
                "- {status} {label}({id}): evidence={evidence}; action={action}".format(
                    status=str(row.get("status") or "").strip() or "unknown",
                    label=str(row.get("label") or "").strip() or "unnamed",
                    id=str(row.get("id") or "").strip() or "unknown",
                    evidence=str(row.get("evidence") or "").strip()[:220] or "-",
                    action=str(row.get("action") or "").strip()[:220] or "-",
                )
            )
        repair_lines: list[str] = []
        for item in list(remediation.get("work_queue") or [])[:8]:
            if not isinstance(item, dict):
                continue
            repair_lines.append(
                "- {priority} {discipline}: hook={hook}, posture={posture}, exit={exit}".format(
                    priority=str(item.get("priority") or "").strip() or "-",
                    discipline=str(item.get("discipline_id") or "").strip()
                    or str(item.get("task_id") or "").strip()
                    or "unknown",
                    hook=str(item.get("automation_hook") or "").strip() or "-",
                    posture=str(item.get("allowed_entry_posture") or "").strip()
                    or str(item.get("lane_policy_hint") or "").strip()
                    or "-",
                    exit=str(item.get("exit_criteria") or "").strip()[:220] or "-",
                )
            )
        score = validation.get("total_score") or summary.get("total_score") or ""
        readiness = str(summary.get("readiness") or "").strip()
        diagnostic_status = str(summary.get("diagnostic_status") or "").strip()
        fail_count = validation.get("fail_count") or summary.get("fail_count") or 0
        warn_count = validation.get("warn_count") or summary.get("warn_count") or 0
        sample_count = metrics.get("sample_count") or summary.get(
            "active_revision_sample_count"
        )
        scale_up_allowed = summary.get("scale_up_allowed")
        primary_next_action = str(remediation.get("primary_next_action") or "").strip()
        risk_state = "\n".join(
            [
                f"- venue={scope}",
                f"- run_id={validation.get('run_id') or ''}",
                f"- computed_at={validation.get('computed_at') or ''}",
                f"- strategy_revision_id={validation.get('strategy_revision_id') or ''}",
                f"- score={score}",
                f"- readiness={readiness or '-'}",
                f"- diagnostic_status={diagnostic_status or '-'}",
                f"- fail_count={fail_count}",
                f"- warn_count={warn_count}",
                f"- sample_count={sample_count or 0}",
                f"- scale_up_allowed={scale_up_allowed}",
                f"- primary_next_action={primary_next_action or '-'}",
            ]
        )
        probe_lanes = [
            str(row).strip()
            for row in list(lane_authority_summary.get("probe_lane_names") or [])
            if str(row).strip()
        ]
        scale_blocked_lanes = [
            str(row).strip()
            for row in list(lane_authority_summary.get("scale_blocked_lanes") or [])
            if str(row).strip()
        ]
        reduced_lanes = self._lane_authority_names(
            lane_authority_summary.get("reduced_lanes")
            or lane_authority_summary.get("reduced_lane_names")
            or []
        )
        reduced_lane_count = lane_authority_summary.get("reduced_lane_count")
        if reduced_lane_count is None:
            reduced_lane_count = len(reduced_lanes)
        lane_authority_lines = [
            f"- execution_posture={lane_authority_summary.get('execution_posture') or '-'}",
            f"- probe_lane_count={lane_authority_summary.get('probe_lane_count') or 0}",
            f"- probe_lanes={', '.join(probe_lanes) if probe_lanes else '-'}",
            f"- reduced_lane_count={reduced_lane_count or 0}",
            f"- reduced_lanes={', '.join(reduced_lanes) if reduced_lanes else '-'}",
            (
                "- scale_blocked_lane_count="
                f"{lane_authority_summary.get('scale_blocked_lane_count') or 0}"
            ),
            (
                "- scale_blocked_lanes="
                f"{', '.join(scale_blocked_lanes) if scale_blocked_lanes else '-'}"
            ),
        ]
        aggression_contract = (
            "- 쥬는 validation이 약한 lane을 포기하지 말고, 원인을 repair queue로 "
            "분해해 다음 블록 설계에 반영한다.\n"
            "- fail discipline이 남아 있으면 무작정 크기를 키우지 말고, "
            "대기진입/비용검증/손익비 개선으로 공격 품질을 먼저 올린다.\n"
            "- core gate가 통과했고 hard blocking이 없으면 표본 축적용 probe와 "
            "조건부 공격 후보는 계속 설계한다."
        )
        next_summary = (
            f"{scope} 판단 시 trading validation score={score}, "
            f"readiness={readiness or '-'}, diagnostic={diagnostic_status or '-'}, "
            f"fail={fail_count}, warn={warn_count}, repair={primary_next_action or '-'}를 "
            "먼저 확인하고, 약한 discipline은 대기진입/수량/손익비/비용검증 조건으로 "
            "블록 설계에 반영한다."
        )
        return {
            "Current Stance": (
                f"{scope} 쥬는 최신 19개 검증 결과를 공격성 조절과 수리 작업의 "
                "핵심 운용 기억으로 사용한다."
            ),
            "Durable Facts": risk_state,
            "Evidence Links": f"- trading_validation:{validation.get('run_id') or 'latest'}",
            "Trading History": (
                f"- validation sample_count={sample_count or 0}, "
                f"total_net_pnl={metrics.get('total_net_pnl') or '-'}, "
                f"win_rate_pct={metrics.get('win_rate_pct') or '-'}, "
                f"profit_factor={metrics.get('profit_factor') or '-'}"
            ),
            "Lessons": (
                "- 검증 실패는 거래 포기의 이유가 아니라, 더 좋은 진입 구조와 "
                "비용/리스크 수리의 입력이다."
            ),
            "Contradictions": (
                "- high activity와 negative expectancy가 동시에 보이면, "
                "활동량보다 실행 품질을 먼저 고친다."
            ),
            "Open Questions": (
                "- 어떤 lane이 실제로 비용/손익비를 개선했는가?\n"
                "- 다음 manager run에서 repair queue의 어떤 항목을 블록 설계에 "
                "직접 반영할 것인가?"
            ),
            "Next Context Pack Summary": next_summary,
            "Risk State": risk_state,
            "Lane Authority": "\n".join(lane_authority_lines)
            if lane_authority_summary
            else "- No lane authority summary.",
            "Failed Disciplines": "\n".join(failed_lines)
            or "- No failed/warn disciplines.",
            "Repair Queue": "\n".join(repair_lines) or "- No repair queue.",
            "Aggression Contract": aggression_contract,
        }

    @staticmethod
    def _derive_lane_authority_summary(
        lane_scorecards: dict[str, Any],
        *,
        allow_scale_up: bool,
    ) -> dict[str, Any]:
        def boolish(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return False
            if isinstance(value, (int, float)):
                return bool(value)
            return str(value).strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
                "on",
                "required",
            }

        def lane_list(value: Any) -> list[str]:
            seen: set[str] = set()
            rows: list[str] = []
            for raw in list(value or []):
                lane = str(raw or "").strip()
                if lane and lane not in seen:
                    seen.add(lane)
                    rows.append(lane)
            return rows

        def append_unique(rows: list[str], lane: str) -> None:
            clean = str(lane or "").strip()
            if clean and clean not in rows:
                rows.append(clean)

        def action_allows_probe(action: dict[str, Any]) -> bool:
            action_text = str(action.get("action") or "").strip().lower()
            reason_text = str(action.get("reason") or action.get("summary") or "").lower()
            grade = str(action.get("grade") or "").strip().lower()
            blocked_tokens = (
                "halt_new_risk",
                "no_live_entry",
                "observe_only_until",
                "risk_off_only",
            )
            if any(token in action_text or token in reason_text for token in blocked_tokens):
                return False
            if any(
                token in action_text or token in reason_text
                for token in (
                    "probe",
                    "waiting_entry",
                    "wait_for_price",
                    "sample_build",
                    "shadow_or_waiting",
                    "small_probe",
                )
            ):
                return True
            if boolish(action.get("requires_waiting_entry")) and grade in {
                "insufficient",
                "restricted",
                "qualified",
                "scale_candidate",
            }:
                return True
            return grade == "insufficient"

        def action_blocks_scale(action: dict[str, Any]) -> bool:
            return any(
                boolish(action.get(key))
                for key in (
                    "scale_up_blocked",
                    "blocks_scale_up",
                    "scale_up_blocked_by_shadow_gate",
                    "scale_up_blocked_by_exposure_gate",
                    "scale_up_blocked_by_validation_remediation",
                    "scale_up_blocked_by_active_revision",
                    "scale_blocked_by_validation_evidence",
                    "scale_blocked_by_validation_repair",
                    "scale_blocked_by_cost_precision",
                    "scale_blocked_by_cost_evidence",
                    "scale_blocked_by_verified_edge_samples",
                    "scale_blocked_by_verified_edge_net_pnl",
                    "scale_blocked_by_entry_quality",
                    "scale_blocked_by_performance_evidence",
                )
            )

        probe_lanes = lane_list(lane_scorecards.get("insufficient_lanes"))
        scale_blocked_lanes: list[str] = []
        for key in (
            "shadow_blocked_lanes",
            "exposure_blocked_lanes",
            "remediation_blocked_lanes",
        ):
            for lane in lane_list(lane_scorecards.get(key)):
                append_unique(scale_blocked_lanes, lane)
        lane_actions = (
            lane_scorecards.get("lane_actions")
            if isinstance(lane_scorecards.get("lane_actions"), dict)
            else {}
        )
        for lane, raw_action in lane_actions.items():
            if not isinstance(raw_action, dict):
                continue
            if action_allows_probe(raw_action):
                append_unique(probe_lanes, str(lane))
            if action_blocks_scale(raw_action):
                append_unique(scale_blocked_lanes, str(lane))
        weak_lanes = lane_list(lane_scorecards.get("weak_lanes"))
        reduced_lanes: list[str] = []
        for lane in [*probe_lanes, *scale_blocked_lanes, *weak_lanes]:
            append_unique(reduced_lanes, lane)
        for key in (
            "cost_evidence_weak_lanes",
            "entry_quality_weak_lanes",
            "validation_evidence_weak_lanes",
            "validation_repair_weak_lanes",
        ):
            for lane in lane_list(lane_scorecards.get(key)):
                append_unique(reduced_lanes, lane)
        if probe_lanes and scale_blocked_lanes:
            execution_posture = "probe_allowed_scale_blocked"
        elif allow_scale_up:
            execution_posture = "scale_allowed"
        elif probe_lanes:
            execution_posture = "probe_allowed_sample_building"
        elif scale_blocked_lanes or weak_lanes:
            execution_posture = "review_required_no_scale"
        else:
            execution_posture = "normal_selective"
        return {
            "status": str(lane_scorecards.get("status") or ""),
            "execution_posture": execution_posture,
            "probe_lane_count": len(probe_lanes),
            "probe_lane_names": probe_lanes[:12],
            "reduced_lane_count": len(reduced_lanes),
            "reduced_lanes": reduced_lanes[:12],
            "scale_blocked_lane_count": len(scale_blocked_lanes),
            "scale_blocked_lanes": scale_blocked_lanes[:12],
        }

    @staticmethod
    def _lane_authority_names(value: Any) -> list[str]:
        rows = value if isinstance(value, list) else []
        out: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                lane = str(row.get("lane") or row.get("name") or "").strip()
            else:
                lane = str(row or "").strip()
            if lane and lane not in out:
                out.append(lane)
        return out

    def _read_binance_symbol_blocks(self) -> dict[str, list[dict[str, Any]]]:
        path = self.config.binance_blocks_db_path
        if path is None:
            return {}
        rows = self._read_rows_by_symbol(
            path=Path(path),
            table="blocks",
            columns={
                "block_id": ["block_id"],
                "symbol": ["symbol"],
                "market": ["market"],
                "lane": ["lane"],
                "side": ["side"],
                "status": ["status"],
                "qty_initial": ["qty_initial", "qty"],
                "entry_price": ["entry_price"],
                "target_price": ["target_price"],
                "stop_price": ["stop_price"],
                "thesis": ["thesis"],
                "llm_reason": ["llm_reason", "reason"],
                "risk_note": ["risk_note"],
                "created_at": ["created_at"],
                "closed_at": ["closed_at"],
                "pnl_usdt": ["pnl_usdt", "net_pnl_usdt", "pnl"],
            },
            order_columns=["closed_at", "updated_at", "created_at"],
            limit=800,
        )
        self._enrich_binance_blocks_from_performance_reflections(
            grouped=rows,
            path=Path(path),
        )
        return rows

    def _read_reflections_by_symbol(
        self,
        *,
        scope: str,
    ) -> dict[str, list[dict[str, Any]]]:
        path = self.config.investment_memory_db_path
        if path is None:
            return {}
        grouped = self._read_rows_by_symbol(
            path=Path(path),
            table="block_reflections",
            columns={
                "block_id": ["block_id"],
                "symbol": ["symbol"],
                "name": ["name"],
                "status": ["status"],
                "summary": ["summary", "summary_md"],
                "lesson": ["lesson", "lesson_md"],
                "metrics_json": ["metrics_json"],
                "created_at": ["created_at", "updated_at"],
            },
            order_columns=["updated_at", "created_at"],
            limit=500,
        )
        filtered: dict[str, list[dict[str, Any]]] = {}
        for symbol, rows in grouped.items():
            for row in rows:
                metrics = self._parse_json(
                    row.get("metrics_json"),
                    {},
                    field=f"block_reflections.metrics_json:{row.get('block_id') or symbol}",
                    allow_missing=True,
                )
                row_scope = str(
                    metrics.get("memory_scope")
                    or metrics.get("scope")
                    or metrics.get("venue")
                    or ""
                ).strip().lower()
                if row_scope != scope:
                    continue
                filtered.setdefault(symbol, []).append(row)
        return filtered

    def _read_rows_by_symbol(
        self,
        *,
        path: Path,
        table: str,
        columns: dict[str, list[str]],
        order_columns: list[str],
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        if not path.exists():
            return {}
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                if not self._table_exists(conn, table):
                    return {}
                table_columns = self._table_columns(conn, table)
                if "symbol" not in table_columns:
                    raise JueWikiSourceReadError(
                        f"source table {table} in {path} is missing symbol column"
                    )
                select_exprs = [
                    self._select_expr(
                        table_columns=table_columns,
                        aliases=aliases,
                        output_name=output_name,
                    )
                    for output_name, aliases in columns.items()
                ]
                order_expr = self._order_expr(
                    table_columns=table_columns,
                    preferred=order_columns,
                )
                rows = conn.execute(
                    f"""
                    SELECT {", ".join(select_exprs)}
                    FROM {table}
                    WHERE symbol IS NOT NULL AND TRIM(symbol) != ''
                    ORDER BY {order_expr} DESC
                    LIMIT ?
                    """,
                    (max(int(limit), 1),),
                ).fetchall()
        except JueWikiSourceReadError:
            raise
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to read source table {table} in {path}: {exc}"
            ) from exc
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = {key: row[key] for key in row.keys()}
            symbol = _normalize_symbol(str(payload.get("symbol") or ""))
            if symbol:
                payload["symbol"] = symbol
                grouped.setdefault(symbol, []).append(payload)
        return grouped

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to inspect source table {table}: {exc}"
            ) from exc
        return row is not None

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.DatabaseError as exc:
            raise JueWikiSourceReadError(
                f"failed to inspect source table {table}: {exc}"
            ) from exc
        return {str(row[1]) for row in rows}

    @staticmethod
    def _select_expr(
        *,
        table_columns: set[str],
        aliases: list[str],
        output_name: str,
    ) -> str:
        for alias in aliases:
            if alias in table_columns:
                return f"{alias} AS {output_name}"
        return f"'' AS {output_name}"

    @staticmethod
    def _order_expr(*, table_columns: set[str], preferred: list[str]) -> str:
        present = [column for column in preferred if column in table_columns]
        if not present:
            return "rowid"
        return "COALESCE(" + ", ".join(present) + ", '')"

    def _enrich_binance_blocks_from_performance_reflections(
        self,
        *,
        grouped: dict[str, list[dict[str, Any]]],
        path: Path,
    ) -> None:
        if not grouped or not path.exists():
            return
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                table_columns = self._table_columns(conn, "block_performance_reflections")
                if "block_id" not in table_columns:
                    return
                select_exprs = [
                    self._select_expr(
                        table_columns=table_columns,
                        aliases=aliases,
                        output_name=output_name,
                    )
                    for output_name, aliases in {
                        "block_id": ["block_id"],
                        "lane": ["lane"],
                        "pnl_usdt": ["net_pnl_usdt", "pnl_usdt"],
                        "market": ["market"],
                        "side": ["side"],
                    }.items()
                ]
                rows = conn.execute(
                    f"""
                    SELECT {", ".join(select_exprs)}
                    FROM block_performance_reflections
                    """
                ).fetchall()
        except sqlite3.Error:
            return
        performance_by_block = {
            str(row["block_id"] or ""): {key: row[key] for key in row.keys()}
            for row in rows
            if str(row["block_id"] or "").strip()
        }
        for block_rows in grouped.values():
            for row in block_rows:
                performance = performance_by_block.get(str(row.get("block_id") or ""))
                if not performance:
                    continue
                for key in ("lane", "pnl_usdt", "market", "side"):
                    if not str(row.get(key) or "").strip() and str(
                        performance.get(key) or ""
                    ).strip():
                        row[key] = performance[key]

    def _build_kis_symbol_sections(
        self,
        *,
        symbol: str,
        name: str,
        blocks: list[dict[str, Any]],
        reflections: list[dict[str, Any]],
        manager_observations: list[dict[str, Any]],
        discovery_notes: list[dict[str, Any]],
        fundamentals: list[dict[str, Any]],
        etf_notes: list[dict[str, Any]],
        strategy_signals: list[dict[str, Any]],
    ) -> dict[str, str]:
        trading_lines: list[str] = []
        evidence_lines: list[str] = []
        lesson_lines: list[str] = []
        observation_lines: list[str] = []
        attention_lines: list[str] = []
        memory_quality_lines: list[str] = []
        evidence_quality_lines: list[str] = []
        discovery_lines: list[str] = []
        research_lines: list[str] = []
        valuation_lines: list[str] = []

        def value_text(value: Any, *, decimals: int | None = None) -> str:
            if value in (None, "", [], {}):
                return "-"
            if decimals is not None:
                try:
                    return f"{float(value):.{decimals}f}"
                except (TypeError, ValueError):
                    return str(value)
            return self._format_ops_number(value)

        for row in blocks[:8]:
            block_id = str(row.get("block_id") or "").strip()
            thesis = str(row.get("thesis") or row.get("llm_reason") or "").strip()
            trading_lines.append(
                "- {block_id}: status={status}, qty={qty}, entry={entry}, "
                "target={target}, stop={stop}, pnl={pnl}, thesis={thesis}".format(
                    block_id=block_id or "unknown",
                    status=row.get("status") or "",
                    qty=row.get("qty_initial") or "",
                    entry=row.get("entry_price") or "",
                    target=row.get("target_price") or "",
                    stop=row.get("stop_price") or "",
                    pnl=row.get("realized_pnl") or "",
                    thesis=thesis,
                )
            )
            if block_id:
                evidence_lines.append(f"- kis_blocks:{block_id}")
        for row in reflections[:8]:
            block_id = str(row.get("block_id") or "").strip()
            summary = str(row.get("summary") or "").strip()
            lesson = str(row.get("lesson") or "").strip()
            if summary:
                lesson_lines.append(f"- {block_id or 'unknown'} summary: {summary}")
            if lesson:
                lesson_lines.append(f"- {block_id or 'unknown'} lesson: {lesson}")
            if block_id:
                evidence_lines.append(f"- investment_memory:{block_id}")
        for row in manager_observations[:8]:
            run_id = str(row.get("manager_run_id") or "").strip()
            run_status = str(row.get("run_status") or "").strip()
            error_message = str(row.get("error_message") or "").strip()
            score = str(row.get("aggressive_score") or "").strip()
            signals = ", ".join(str(item) for item in list(row.get("signals") or [])[:4])
            sources = ", ".join(str(item) for item in list(row.get("sources") or [])[:4])
            stance = str(row.get("stance") or "").strip()
            confidence = str(row.get("confidence") or "").strip()
            summary = str(row.get("summary") or "").strip()
            no_action_reason = str(row.get("no_action_reason") or "").strip()
            hold_summary = str(row.get("hold_summary") or "").strip()
            pressure = "/".join(
                item
                for item in (
                    str(row.get("proactive_status") or "").strip(),
                    str(row.get("proactive_level") or "").strip(),
                )
                if item
            )
            streak = str(row.get("zero_action_streak") or "").strip()
            required_resolution = str(row.get("required_resolution") or "").strip()
            observation_lines.append(
                "- manager_run={run_id}, session={session}, run_status={run_status}, "
                "error={error}, source={source}, "
                "score={score}, confidence={confidence}, stance={stance}, "
                "signals={signals}, sources={sources}, no_action={no_action}, "
                "pressure={pressure}, streak={streak}, hold={hold}, "
                "required={required}, summary={summary}".format(
                    run_id=run_id or "unknown",
                    session=row.get("market_session") or "",
                    run_status=run_status or "-",
                    error=error_message[:180] or "-",
                    source=row.get("source_type") or "",
                    score=score or "-",
                    confidence=confidence or "-",
                    stance=stance or "-",
                    signals=signals or "-",
                    sources=sources or "-",
                    no_action=no_action_reason or "-",
                    pressure=pressure or "-",
                    streak=streak or "-",
                    hold=hold_summary or "-",
                    required=required_resolution[:180] or "-",
                    summary=summary[:220] or "-",
                )
            )
            attention_status = str(row.get("wiki_attention_status") or "").strip()
            attention_resolution = str(
                row.get("wiki_attention_resolution") or ""
            ).strip()
            if attention_status or attention_resolution:
                must_address = ", ".join(
                    str(item)
                    for item in list(row.get("wiki_attention_must_address") or [])[:4]
                )
                targets = ", ".join(
                    str(item)
                    for item in list(row.get("wiki_attention_targets") or [])[:4]
                )
                attention_line = (
                    "- manager_run={run_id}, observed_at={observed_at}, "
                    "status={status}, "
                    "resolution={resolution}, component={component}, "
                    "action={action}, must={must}, targets={targets}, "
                    "recommended={recommended}"
                ).format(
                    run_id=run_id or "unknown",
                    observed_at=str(row.get("observed_at") or "").strip() or "-",
                    status=attention_status or "-",
                    resolution=attention_resolution or "-",
                    component=str(row.get("wiki_attention_component") or "").strip()
                    or "-",
                    action=str(row.get("wiki_attention_action_type") or "").strip()
                    or "-",
                    must=must_address or "-",
                    targets=targets or "-",
                    recommended=str(row.get("wiki_attention_recommended") or "")
                    .strip()[:220]
                    or "-",
                )
                if attention_line not in attention_lines:
                    attention_lines.append(attention_line)
            memory_quality_status = str(
                row.get("wiki_memory_card_quality_status") or ""
            ).strip()
            memory_quality_resolution = str(
                row.get("wiki_memory_card_quality_resolution") or ""
            ).strip()
            if memory_quality_status or memory_quality_resolution:
                quality_symbols = ", ".join(
                    str(item)
                    for item in list(
                        row.get("wiki_memory_card_quality_symbols") or []
                    )[:4]
                )
                missing_fields = "|".join(
                    str(item).strip().replace(",", ";")
                    for item in list(
                        row.get("wiki_memory_card_quality_missing_fields") or []
                    )[:8]
                    if str(item).strip()
                )
                required_checks = "|".join(
                    str(item).strip().replace(",", ";")
                    for item in list(
                        row.get("wiki_memory_card_quality_required_checks") or []
                    )[:8]
                    if str(item).strip()
                )
                quality_line = (
                    "- manager_run={run_id}, observed_at={observed_at}, "
                    "status={status}, resolution={resolution}, symbols={symbols}, "
                    "required={required}, missing_fields={missing_fields}, "
                    "required_checks={required_checks}"
                ).format(
                    run_id=run_id or "unknown",
                    observed_at=str(row.get("observed_at") or "").strip() or "-",
                    status=memory_quality_status or "-",
                    resolution=memory_quality_resolution or "-",
                    symbols=quality_symbols or "-",
                    required=str(
                        row.get("wiki_memory_card_quality_required_action") or ""
                    ).strip()[:220]
                    or "-",
                    missing_fields=missing_fields or "-",
                    required_checks=required_checks or "-",
                )
                if quality_line not in memory_quality_lines:
                    memory_quality_lines.append(quality_line)
            evidence_quality_summary = str(
                row.get("wiki_evidence_quality_summary") or ""
            ).strip()
            evidence_quality_counts = (
                row.get("wiki_evidence_quality_status_counts")
                if isinstance(row.get("wiki_evidence_quality_status_counts"), dict)
                else {}
            )
            evidence_quality_warnings = [
                str(item).strip()
                for item in list(row.get("wiki_evidence_quality_warnings") or [])[:6]
                if str(item).strip()
            ]
            if (
                evidence_quality_summary
                or evidence_quality_counts
                or evidence_quality_warnings
            ):
                counts_text = ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(evidence_quality_counts.items())
                )
                quality_line = (
                    "- manager_run={run_id}, observed_at={observed_at}, "
                    "summary={summary}, counts={counts}, warnings={warnings}"
                ).format(
                    run_id=run_id or "unknown",
                    observed_at=str(row.get("observed_at") or "").strip() or "-",
                    summary=evidence_quality_summary or "-",
                    counts=counts_text or "-",
                    warnings=", ".join(evidence_quality_warnings) or "-",
                )
                if quality_line not in evidence_quality_lines:
                    evidence_quality_lines.append(quality_line)
            if run_id:
                evidence_lines.append(f"- kis_manager_runs:{run_id}")
        if any(
            str(row.get("run_status") or "").strip().lower() == "error"
            for row in manager_observations
        ):
            lesson_lines.append(
                "- Manager run errors are repair memory: shrink context, keep "
                "aggressive candidates and research evidence, and rerun so the "
                "candidate can become a block or an explicit rejection."
            )
        for row in discovery_notes[:8]:
            trading_day = str(row.get("trading_day") or "").strip()
            pre_surge = (
                row.get("pre_surge") if isinstance(row.get("pre_surge"), dict) else {}
            )
            pre_surge_reasons = ", ".join(
                str(item) for item in list(pre_surge.get("reasons") or [])[:4]
            )
            analysis_reasons = ", ".join(
                str(item) for item in list(row.get("reasons") or [])[:4]
            )
            risks = ", ".join(str(item) for item in list(row.get("risks") or [])[:3])
            discovery_lines.append(
                "- {day}: market={market}, stance={stance}, confidence={confidence}, "
                "score={score}, pre_surge={pre_surge}, entry_bias={entry_bias}, "
                "preferred_horizon={preferred_horizon}, reasons={reasons}, "
                "risks={risks}, summary={summary}".format(
                    day=trading_day or "unknown",
                    market=row.get("market") or "",
                    stance=row.get("stance") or "",
                    confidence=row.get("confidence") or "",
                    score=row.get("score") or "",
                    pre_surge=pre_surge.get("is_candidate")
                    if pre_surge
                    else "",
                    entry_bias=pre_surge.get("entry_bias") or "-",
                    preferred_horizon=pre_surge.get("preferred_horizon") or "-",
                    reasons=pre_surge_reasons or analysis_reasons or "-",
                    risks=risks or "-",
                    summary=str(row.get("summary") or "").strip()[:240] or "-",
                )
            )
            if trading_day:
                evidence_lines.append(f"- daily_discovery:{trading_day}")
        for row in fundamentals[:3]:
            reasons = ", ".join(
                str(item).strip()
                for item in list(row.get("score_reasons") or [])[:3]
                if str(item).strip()
            )
            risks = ", ".join(
                str(item).strip()
                for item in list(row.get("score_risks") or [])[:3]
                if str(item).strip()
            )
            financial_rows = []
            for financial in list(row.get("financials") or [])[:2]:
                financial_rows.append(
                    "{period_type}:{period} revenue={revenue}, op={op}, "
                    "net={net}, roe={roe}, debt={debt}".format(
                        period_type=financial.get("period_type") or "-",
                        period=financial.get("period") or "-",
                        revenue=value_text(financial.get("revenue")),
                        op=value_text(financial.get("operating_profit")),
                        net=value_text(financial.get("net_income")),
                        roe=value_text(financial.get("roe"), decimals=2),
                        debt=value_text(financial.get("debt_ratio"), decimals=2),
                    )
                )
            valuation_lines.append(
                "- valuation quality={quality}, warnings={warnings}, "
                "label={label}, status={status}, as_of={as_of}, "
                "price={price}, market_cap={market_cap}, PER={per}, EPS={eps}, "
                "PBR={pbr}, BPS={bps}, dividend_yield={dividend}, "
                "industry={industry}, industry_PER={industry_per}, "
                "relative_PER_discount={discount}, undervalued={undervalued}, "
                "overvalued_risk={overvalued}, quality_score={quality_score}, "
                "growth={growth}; "
                "reasons={reasons}; risks={risks}; financials={financials}".format(
                    quality=_normalize_quality_status(row.get("quality_status"))
                    or "unknown",
                    warnings=", ".join(
                        str(item)
                        for item in list(row.get("quality_warnings") or [])[:6]
                    )
                    or "-",
                    label=row.get("valuation_label") or "unknown",
                    status=row.get("status") or "-",
                    as_of=row.get("as_of") or "-",
                    price=value_text(row.get("price")),
                    market_cap=value_text(row.get("market_cap_krw")),
                    per=value_text(row.get("per"), decimals=2),
                    eps=value_text(row.get("eps")),
                    pbr=value_text(row.get("pbr"), decimals=2),
                    bps=value_text(row.get("bps")),
                    dividend=value_text(row.get("dividend_yield_pct"), decimals=2),
                    industry=row.get("industry_name") or "-",
                    industry_per=value_text(row.get("industry_per"), decimals=2),
                    discount=value_text(
                        row.get("relative_per_discount_pct"),
                        decimals=2,
                    ),
                    undervalued=value_text(row.get("undervalued_score")),
                    overvalued=value_text(row.get("overvalued_risk")),
                    quality_score=value_text(row.get("quality_score")),
                    growth=value_text(row.get("growth_score")),
                    reasons=reasons or "-",
                    risks=risks or "-",
                    financials=" | ".join(financial_rows) if financial_rows else "-",
                )
            )
            source_id = str(row.get("source_id") or row.get("symbol") or "").strip()
            if source_id:
                evidence_lines.append(f"- symbol_fundamentals:{source_id}")
        for row in etf_notes[:8]:
            reasons = ", ".join(str(item) for item in list(row.get("reasons") or [])[:3])
            risks = ", ".join(str(item) for item in list(row.get("risks") or [])[:3])
            research_lines.append(
                "- etf label={label}, category={category}, price={price}, "
                "change_pct={change}, turnover={turnover}, liquidity={liquidity}, "
                "momentum={momentum}, core_fit={core_fit}, risk={risk}; "
                "reasons={reasons}; risks={risks}".format(
                    label=row.get("label") or "-",
                    category=row.get("category") or "-",
                    price=row.get("price") or "-",
                    change=row.get("change_pct") or "-",
                    turnover=row.get("turnover_krw") or "-",
                    liquidity=row.get("liquidity_score") or "-",
                    momentum=row.get("momentum_score") or "-",
                    core_fit=row.get("core_fit_score") or "-",
                    risk=row.get("risk_score") or "-",
                    reasons=reasons or "-",
                    risks=risks or "-",
                )
            )
            source_id = str(row.get("source_id") or row.get("symbol") or "").strip()
            if source_id:
                evidence_lines.append(f"- etf_research:{source_id}")
        for row in strategy_signals[:10]:
            tags = ", ".join(str(item) for item in list(row.get("tags") or [])[:4])
            research_lines.append(
                "- insight source={source}, type={signal_type}, direction={direction}, "
                "strength={strength}, as_of={as_of}; tags={tags}; summary={summary}".format(
                    source=row.get("source_id") or "-",
                    signal_type=row.get("signal_type") or "-",
                    direction=row.get("direction") or "-",
                    strength=row.get("strength") or "-",
                    as_of=row.get("as_of") or row.get("observed_at") or "-",
                    tags=tags or "-",
                    summary=str(row.get("summary") or "").strip()[:260] or "-",
                )
            )
            signal_id = str(row.get("signal_id") or "").strip()
            if signal_id:
                evidence_lines.append(f"- strategy_insight:{signal_id}")
        if observation_lines:
            lesson_lines.append(
                "- Manager observations are pre-trade memory: if repeated "
                "aggressive candidates do not become blocks, review missing "
                "execution conditions instead of ignoring the symbol."
            )
            if any(
                "rejected" in str(row.get("source_type") or "")
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- 게이트가 거절한 적극 제안은 보수화 신호가 아니라 실행 설계 "
                    "수정 재료다: 다음 KIS 판단에서는 거절 사유를 가격 구조, "
                    "horizon, 수량, 증거 품질 중 어느 축에서 고칠지 명시한다."
                )
            if any(
                str(row.get("proactive_status") or "") == "action_required"
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- Action-required pressure is unresolved memory: next KIS "
                    "manager cycles must convert it into an executable waiting/probe "
                    "block or a candidate-level rejection condition."
                )
            if any(
                str(row.get("wiki_attention_resolution") or "").strip().lower()
                == "unresolved"
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- Jue Wiki attention is unresolved repair memory: the next KIS "
                    "manager run must explicitly resolve, probe, or reject the named "
                    "repair target instead of letting the wiki warning disappear."
                )
            if any(
                str(row.get("wiki_memory_card_quality_resolution") or "")
                .strip()
                .lower()
                == "unresolved"
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- Jue Wiki memory card quality is unresolved repair memory: "
                    "weak symbol memory must be cross-checked with live research, "
                    "fundamentals, flow, and block history before high-confidence "
                    "KIS block creation."
                )
            if evidence_quality_lines:
                lesson_lines.append(
                    "- Jue Wiki evidence quality is manager-context repair memory: "
                    "weak or partial evidence must become a named data refresh, "
                    "smaller probe, waiting trigger, or explicit candidate reject."
                )
        if discovery_lines:
            lesson_lines.append(
                "- Daily discovery is pre-trade research memory: convert repeated "
                "pre-surge/value-cycle observations into explicit waiting-entry "
                "conditions, or record why the setup is not executable yet."
            )
        if valuation_lines:
            lesson_lines.append(
                "- Naver/WiseReport fundamentals are mid/long-horizon valuation "
                "memory: use them to judge cheap/expensive pressure, but require "
                "live price structure before creating an executable block."
            )
        if etf_notes:
            lesson_lines.append(
                "- ETF research is tradable symbol memory: use liquidity, turnover, "
                "and regime fit to create core/mid/attack ETF blocks instead of "
                "treating ETF as a separate non-tradable category."
            )
        if strategy_signals:
            lesson_lines.append(
                "- Whale/Sesiban style strategy signals are independent flow memory: "
                "when repeated, convert them into a timing trigger or an explicit "
                "rejection condition."
            )
        memory_quality_context = ""
        if memory_quality_lines:
            memory_quality_context = (
                " Memory card quality repair active: "
                + " ".join(line.removeprefix("- ").strip() for line in memory_quality_lines[:2])
            )
        return {
            "Current Stance": (
                f"{name}({symbol})는 블록 거래 이력, 반성 기록, "
                "매니저의 공격 후보 관측, daily discovery 리서치를 함께 "
                "기준으로 재평가한다."
            ),
            "Durable Facts": f"- scope=kis\n- symbol={symbol}\n- name={name}",
            "Evidence Links": "\n".join(
                [
                    *evidence_lines,
                    *(
                        ["### Daily Discovery Research", *discovery_lines]
                        if discovery_lines
                        else []
                    ),
                    *(
                        ["### Naver / WiseReport Fundamentals", *valuation_lines]
                        if valuation_lines
                        else []
                    ),
                    *(
                        ["### ETF / Flow Research", *research_lines]
                        if research_lines
                        else []
                    ),
                ]
            )
            or "- No linked evidence.",
            "Trading History": "\n".join(
                [
                    *trading_lines,
                    *(
                        ["### Manager Opportunity Observations", *observation_lines]
                        if observation_lines
                        else []
                    ),
                    *(
                        ["### Jue Wiki Attention", *attention_lines]
                        if attention_lines
                        else []
                    ),
                    *(
                        [
                            "### Jue Wiki Memory Card Quality",
                            *memory_quality_lines,
                        ]
                        if memory_quality_lines
                        else []
                    ),
                    *(
                        [
                            "### Jue Wiki Evidence Quality",
                            *evidence_quality_lines,
                        ]
                        if evidence_quality_lines
                        else []
                    ),
                ]
            )
            or "- No trading history.",
            "Lessons": "\n".join(lesson_lines) or "- No lessons yet.",
            "Contradictions": "- No active contradiction.",
            "Open Questions": (
                "- 최신 리서치, 밸류, 수급이 거래 교훈과 일치하는지 확인한다.\n"
                "- 공격 후보로 반복 관측된 경우 대기블록/1주 프로브/기각 조건 중 "
                "무엇이 적절한지 명시한다.\n"
                "- 밸류가 매력적인데 가격 구조가 없으면 즉시매수 대신 "
                "대기진입/눌림조건/분할 블록으로 설계한다.\n"
                "- daily discovery가 반복 포착한 종목은 저점/눌림/돌파 조건을 "
                "구체 가격 구조로 바꿀 수 있는지 확인한다."
            ),
            "Next Context Pack Summary": (
                f"{name} 판단 시 거래 이력, 최근 반성, 매니저 공격 후보 관측, "
                "daily discovery, ETF/수급 신호, 최신 리서치/밸류를 함께 사용한다."
                f"{memory_quality_context}"
            ),
        }

    def _build_binance_symbol_sections(
        self,
        *,
        symbol: str,
        blocks: list[dict[str, Any]],
        reflections: list[dict[str, Any]],
        manager_observations: list[dict[str, Any]],
        quant_signals: list[dict[str, Any]],
        pattern_notes: list[dict[str, Any]],
        alpha_events: list[dict[str, Any]],
    ) -> dict[str, str]:
        trading_lines: list[str] = []
        evidence_lines: list[str] = []
        lesson_lines: list[str] = []
        observation_lines: list[str] = []
        attention_lines: list[str] = []
        memory_quality_lines: list[str] = []
        evidence_quality_lines: list[str] = []
        research_lines: list[str] = []
        for row in blocks[:10]:
            block_id = str(row.get("block_id") or "").strip()
            thesis = str(row.get("thesis") or row.get("llm_reason") or "").strip()
            trading_lines.append(
                "- {block_id}: market={market}, lane={lane}, side={side}, "
                "status={status}, qty={qty}, entry={entry}, target={target}, "
                "stop={stop}, pnl_usdt={pnl}, thesis={thesis}".format(
                    block_id=block_id or "unknown",
                    market=row.get("market") or "",
                    lane=row.get("lane") or "",
                    side=row.get("side") or "",
                    status=row.get("status") or "",
                    qty=row.get("qty_initial") or "",
                    entry=row.get("entry_price") or "",
                    target=row.get("target_price") or "",
                    stop=row.get("stop_price") or "",
                    pnl=row.get("pnl_usdt") or "",
                    thesis=thesis,
                )
            )
            if block_id:
                evidence_lines.append(f"- binance_blocks:{block_id}")
        for row in reflections[:8]:
            block_id = str(row.get("block_id") or "").strip()
            summary = str(row.get("summary") or "").strip()
            lesson = str(row.get("lesson") or "").strip()
            if summary:
                lesson_lines.append(f"- {block_id or 'unknown'} summary: {summary}")
            if lesson:
                lesson_lines.append(f"- {block_id or 'unknown'} lesson: {lesson}")
            if block_id:
                evidence_lines.append(f"- investment_memory:{block_id}")
        for row in manager_observations[:10]:
            run_id = str(row.get("manager_run_id") or "").strip()
            run_status = str(row.get("run_status") or "").strip()
            error_message = str(row.get("error_message") or "").strip()
            data_gaps = ", ".join(
                str(item) for item in list(row.get("data_gaps") or [])[:3]
            )
            signals = ", ".join(str(item) for item in list(row.get("signals") or [])[:4])
            sources = ", ".join(str(item) for item in list(row.get("sources") or [])[:4])
            observation_lines.append(
                "- manager_run={run_id}, run_status={run_status}, "
                "error={error}, source={source}, market={market}, "
                "lane={lane}, side={side}, horizon={horizon}, score={score}, "
                "confidence={confidence}, entry={operator}{trigger}, target={target}, "
                "stop={stop}, rr={rr}, crosscheck={crosscheck}/{mode}, "
                "pressure={pressure}/{level}, streak={streak}, condition={condition}, "
                "reason={reason}, signals={signals}, sources={sources}, gaps={gaps}".format(
                    run_id=run_id or "unknown",
                    run_status=run_status or "-",
                    error=error_message[:180] or "-",
                    source=row.get("source_type") or "",
                    market=row.get("market") or "-",
                    lane=row.get("lane") or "-",
                    side=row.get("side") or "-",
                    horizon=row.get("horizon") or "-",
                    score=row.get("score") or "-",
                    confidence=row.get("confidence") or "-",
                    operator=row.get("entry_operator") or "",
                    trigger=row.get("entry_trigger_price") or row.get("entry_price") or "-",
                    target=row.get("target_price") or "-",
                    stop=row.get("stop_price") or "-",
                    rr=row.get("reward_risk") or "-",
                    crosscheck=row.get("crosscheck_status") or "-",
                    mode=row.get("crosscheck_mode") or "-",
                    pressure=row.get("proactive_status") or "-",
                    level=row.get("proactive_level") or "-",
                    streak=row.get("zero_action_streak") or "-",
                    condition=str(row.get("condition") or "").strip()[:180] or "-",
                    reason=str(row.get("reason") or row.get("hold_summary") or "").strip()[:220]
                    or "-",
                    signals=signals or "-",
                    sources=sources or "-",
                    gaps=data_gaps or "-",
                )
            )
            attention_status = str(row.get("wiki_attention_status") or "").strip()
            attention_resolution = str(
                row.get("wiki_attention_resolution") or ""
            ).strip()
            if attention_status or attention_resolution:
                must_address = ", ".join(
                    str(item)
                    for item in list(row.get("wiki_attention_must_address") or [])[:4]
                )
                targets = ", ".join(
                    str(item)
                    for item in list(row.get("wiki_attention_targets") or [])[:4]
                )
                attention_line = (
                    "- manager_run={run_id}, observed_at={observed_at}, "
                    "status={status}, "
                    "resolution={resolution}, component={component}, "
                    "action={action}, must={must}, targets={targets}, "
                    "recommended={recommended}"
                ).format(
                    run_id=run_id or "unknown",
                    observed_at=str(row.get("observed_at") or "").strip() or "-",
                    status=attention_status or "-",
                    resolution=attention_resolution or "-",
                    component=str(row.get("wiki_attention_component") or "").strip()
                    or "-",
                    action=str(row.get("wiki_attention_action_type") or "").strip()
                    or "-",
                    must=must_address or "-",
                    targets=targets or "-",
                    recommended=str(row.get("wiki_attention_recommended") or "")
                    .strip()[:220]
                    or "-",
                )
                if attention_line not in attention_lines:
                    attention_lines.append(attention_line)
            memory_quality_status = str(
                row.get("wiki_memory_card_quality_status") or ""
            ).strip()
            memory_quality_resolution = str(
                row.get("wiki_memory_card_quality_resolution") or ""
            ).strip()
            if memory_quality_status or memory_quality_resolution:
                quality_symbols = ", ".join(
                    str(item)
                    for item in list(
                        row.get("wiki_memory_card_quality_symbols") or []
                    )[:4]
                )
                missing_fields = "|".join(
                    str(item).strip().replace(",", ";")
                    for item in list(
                        row.get("wiki_memory_card_quality_missing_fields") or []
                    )[:8]
                    if str(item).strip()
                )
                required_checks = "|".join(
                    str(item).strip().replace(",", ";")
                    for item in list(
                        row.get("wiki_memory_card_quality_required_checks") or []
                    )[:8]
                    if str(item).strip()
                )
                quality_line = (
                    "- manager_run={run_id}, observed_at={observed_at}, "
                    "status={status}, resolution={resolution}, symbols={symbols}, "
                    "required={required}, missing_fields={missing_fields}, "
                    "required_checks={required_checks}"
                ).format(
                    run_id=run_id or "unknown",
                    observed_at=str(row.get("observed_at") or "").strip() or "-",
                    status=memory_quality_status or "-",
                    resolution=memory_quality_resolution or "-",
                    symbols=quality_symbols or "-",
                    required=str(
                        row.get("wiki_memory_card_quality_required_action") or ""
                    ).strip()[:220]
                    or "-",
                    missing_fields=missing_fields or "-",
                    required_checks=required_checks or "-",
                )
                if quality_line not in memory_quality_lines:
                    memory_quality_lines.append(quality_line)
            evidence_quality_summary = str(
                row.get("wiki_evidence_quality_summary") or ""
            ).strip()
            evidence_quality_counts = (
                row.get("wiki_evidence_quality_status_counts")
                if isinstance(row.get("wiki_evidence_quality_status_counts"), dict)
                else {}
            )
            evidence_quality_warnings = [
                str(item).strip()
                for item in list(row.get("wiki_evidence_quality_warnings") or [])[:6]
                if str(item).strip()
            ]
            if (
                evidence_quality_summary
                or evidence_quality_counts
                or evidence_quality_warnings
            ):
                counts_text = ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(evidence_quality_counts.items())
                )
                quality_line = (
                    "- manager_run={run_id}, observed_at={observed_at}, "
                    "summary={summary}, counts={counts}, warnings={warnings}"
                ).format(
                    run_id=run_id or "unknown",
                    observed_at=str(row.get("observed_at") or "").strip() or "-",
                    summary=evidence_quality_summary or "-",
                    counts=counts_text or "-",
                    warnings=", ".join(evidence_quality_warnings) or "-",
                )
                if quality_line not in evidence_quality_lines:
                    evidence_quality_lines.append(quality_line)
            if run_id:
                evidence_lines.append(f"- binance_manager_runs:{run_id}")
        if any(
            str(row.get("run_status") or "").strip().lower() == "error"
            for row in manager_observations
        ):
            lesson_lines.append(
                "- Manager run errors are repair memory: shrink context, preserve "
                "executable candidate evidence, and rerun instead of letting the "
                "candidate disappear from Jue Wiki."
            )
        for row in quant_signals[:8]:
            signal = row.get("signal") if isinstance(row.get("signal"), dict) else {}
            drivers = ", ".join(str(item) for item in list(signal.get("drivers") or [])[:3])
            risks = ", ".join(str(item) for item in list(signal.get("risks") or [])[:3])
            metrics = signal.get("metrics") if isinstance(signal.get("metrics"), dict) else {}
            research_lines.append(
                "- quant horizon={horizon}, bias={bias}, long={long}, short={short}, "
                "no_trade={no_trade}, expected_r_long={erl}, expected_r_short={ers}, "
                "rsi={rsi}, atr_pct={atr}, spread_bps={spread}; drivers={drivers}; "
                "risks={risks}".format(
                    horizon=row.get("horizon") or "-",
                    bias=signal.get("bias") or "-",
                    long=row.get("long_score") or "-",
                    short=row.get("short_score") or "-",
                    no_trade=row.get("no_trade_score") or "-",
                    erl=row.get("expected_r_long") or "-",
                    ers=row.get("expected_r_short") or "-",
                    rsi=metrics.get("rsi") or "-",
                    atr=metrics.get("atr_pct") or "-",
                    spread=metrics.get("spread_bps") or "-",
                    drivers=drivers or "-",
                    risks=risks or "-",
                )
            )
            source_id = str(row.get("source_id") or "").strip()
            if source_id:
                evidence_lines.append(f"- crypto_quant:{source_id}")
        for row in pattern_notes[:8]:
            params = row.get("parameters") if isinstance(row.get("parameters"), dict) else {}
            research_lines.append(
                "- pattern set={set_id}, family={family}, direction={direction}, "
                "interval={interval}, trades={trades}, win_rate={win_rate}, "
                "expectancy_r={expectancy}, pf={pf}, oos_expectancy={oos}, "
                "overfit={overfit}, stop={stop}, target={target}".format(
                    set_id=row.get("set_id") or "-",
                    family=row.get("family") or "-",
                    direction=row.get("direction") or "-",
                    interval=row.get("interval") or "-",
                    trades=row.get("trade_count") or "-",
                    win_rate=row.get("win_rate") or "-",
                    expectancy=row.get("expectancy_r") or "-",
                    pf=row.get("profit_factor") or "-",
                    oos=row.get("out_of_sample_expectancy_r") or "-",
                    overfit=row.get("overfit_risk") or "-",
                    stop=params.get("stop_pct") or "-",
                    target=params.get("target_pct") or "-",
                )
            )
            source_id = str(row.get("set_id") or row.get("pattern_id") or "").strip()
            if source_id:
                evidence_lines.append(f"- crypto_pattern_lab:{source_id}")
        for row in alpha_events[:6]:
            research_lines.append(
                "- alpha event={event_id}, type={event_type}, direction={direction}, "
                "horizon={horizon}, confidence={confidence}, importance={importance}; "
                "title={title}; reason={reason}".format(
                    event_id=row.get("event_id") or "-",
                    event_type=row.get("event_type") or "-",
                    direction=row.get("impact_direction") or "-",
                    horizon=row.get("impact_horizon") or "-",
                    confidence=row.get("confidence") or row.get("link_confidence") or "-",
                    importance=row.get("importance") or "-",
                    title=str(row.get("title") or "").strip()[:120] or "-",
                    reason=str(row.get("reason") or "").strip()[:180] or "-",
                )
            )
            event_id = str(row.get("event_id") or "").strip()
            if event_id:
                evidence_lines.append(f"- crypto_alpha:{event_id}")
        if observation_lines:
            lesson_lines.append(
                "- Manager candidate observations are pre-trade memory: repeated "
                "research-only, book-error, or no-pattern-prior cases must be "
                "converted into executable wait conditions or explicitly rejected."
            )
            if any(
                "rejected" in str(row.get("source_type") or "")
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- 게이트가 거절한 적극 제안은 보수화 신호가 아니라 실행 설계 "
                    "수정 재료다: 다음 Binance 판단에서는 거절 사유를 lane, "
                    "side, price geometry, live authority, 비용 edge 중 어느 축에서 "
                    "고칠지 명시한다."
                )
            if any(
                str(row.get("proactive_status") or "") == "action_required"
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- Action-required pressure is unresolved memory: next Binance "
                    "manager cycles must convert it into an executable waiting/probe "
                    "block or a candidate-level rejection condition."
                )
            if any(
                str(row.get("wiki_attention_resolution") or "").strip().lower()
                == "unresolved"
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- Jue Wiki attention is unresolved repair memory: the next "
                    "Binance manager run must explicitly resolve, probe, or reject "
                    "the named repair target instead of letting the wiki warning "
                    "disappear."
                )
            if any(
                str(row.get("wiki_memory_card_quality_resolution") or "")
                .strip()
                .lower()
                == "unresolved"
                for row in manager_observations
            ):
                lesson_lines.append(
                    "- Jue Wiki memory card quality is unresolved repair memory: "
                    "weak crypto symbol memory must be cross-checked with crypto "
                    "research, quant evidence, orderbook/funding context, and "
                    "block history before leverage or high-confidence entries."
                )
            if evidence_quality_lines:
                lesson_lines.append(
                    "- Jue Wiki evidence quality is manager-context repair memory: "
                    "weak or partial crypto evidence must become a data refresh, "
                    "smaller probe, waiting trigger, or explicit lane-level reject."
                )
        if quant_signals or pattern_notes:
            lesson_lines.append(
                "- Quant and pattern memory are execution geometry inputs: use them "
                "to set direction, wait trigger, stop width, and target/holding-time "
                "instead of placing tiny discretionary probes without edge evidence."
            )
        if alpha_events:
            lesson_lines.append(
                "- Alpha events are catalyst memory: trade only when live book, "
                "funding, spread, and quant confirm that the catalyst is still active."
            )
        memory_quality_context = ""
        if memory_quality_lines:
            memory_quality_context = (
                " Memory card quality repair active: "
                + " ".join(line.removeprefix("- ").strip() for line in memory_quality_lines[:2])
            )
        return {
            "Current Stance": (
                f"{symbol}는 spot/futures, lane, side별 성과와 manager 후보 관측을 "
                "함께 분리해서 본다."
            ),
            "Durable Facts": f"- scope=binance\n- symbol={symbol}",
            "Evidence Links": "\n".join(evidence_lines) or "- No linked evidence.",
            "Trading History": "\n".join(
                [
                    *trading_lines,
                    *(
                        ["### Manager Candidate Observations", *observation_lines]
                        if observation_lines
                        else []
                    ),
                    *(
                        ["### Jue Wiki Attention", *attention_lines]
                        if attention_lines
                        else []
                    ),
                    *(
                        [
                            "### Jue Wiki Memory Card Quality",
                            *memory_quality_lines,
                        ]
                        if memory_quality_lines
                        else []
                    ),
                    *(
                        [
                            "### Jue Wiki Evidence Quality",
                            *evidence_quality_lines,
                        ]
                        if evidence_quality_lines
                        else []
                    ),
                    *(
                        ["### Quant / Pattern / Alpha Research", *research_lines]
                        if research_lines
                        else []
                    ),
                ]
            )
            or "- No trading history.",
            "Lessons": "\n".join(lesson_lines) or "- No lessons yet.",
            "Contradictions": "- Check long/short bias and spot underuse before new blocks.",
            "Open Questions": (
                "- 레짐, 펀딩, 오더북, 스프레드가 블록 방향과 일치하는지 확인한다.\n"
                "- 반복 관망 후보는 가격 구조, order book 신선도, pattern prior, "
                "spot/futures lane 중 무엇이 막았는지 분리한다."
            ),
            "Next Context Pack Summary": (
                f"{symbol} 판단 시 lane별 성과, 롱/숏 편향, 비용 근거, "
                "최근 manager 후보 관측, quant/pattern/alpha 근거와 관망 사유를 "
                "같이 확인한다."
                f"{memory_quality_context}"
            ),
        }

    def _record_run(
        self,
        *,
        kind: str,
        scope: str,
        status: str,
        updated_count: int,
        warning_count: int = 0,
        error_message: str = "",
    ) -> None:
        now = _utc_now_iso()
        run_id = f"{kind}:{scope}:{now}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wiki_runs (
                    run_id, kind, scope, status, page_count, updated_count,
                    warning_count, error_message, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    kind,
                    scope,
                    status,
                    updated_count,
                    updated_count,
                    warning_count,
                    error_message,
                    now,
                    now,
                ),
            )

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wiki_pages (
                    page_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    page_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    path TEXT NOT NULL,
                    symbols_json TEXT NOT NULL DEFAULT '[]',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.0,
                    freshness TEXT NOT NULL DEFAULT 'unknown',
                    token_estimate INTEGER NOT NULL DEFAULT 0,
                    char_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_source_refs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    source_scope TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(page_id, source_type, source_id)
                );
                CREATE TABLE IF NOT EXISTS wiki_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    updated_count INTEGER NOT NULL DEFAULT 0,
                    warning_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS wiki_lint_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    finding_id TEXT NOT NULL UNIQUE,
                    page_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    finding_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS wiki_selection_runs (
                    run_id TEXT PRIMARY KEY,
                    target_scope TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    budget_report_json TEXT NOT NULL DEFAULT '{}',
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    rejected_count INTEGER NOT NULL DEFAULT 0,
                    char_count INTEGER NOT NULL DEFAULT 0,
                    max_chars INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_selection_pages (
                    run_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    score REAL NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    penalties_json TEXT NOT NULL DEFAULT '[]',
                    char_count INTEGER NOT NULL DEFAULT 0,
                    included INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, page_id)
                );
                CREATE TABLE IF NOT EXISTS wiki_repair_actions (
                    action_id TEXT PRIMARY KEY,
                    finding_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS wiki_playbook_metrics (
                    page_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    playbook_id TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0.0,
                    expectancy REAL NOT NULL DEFAULT 0.0,
                    profit_factor REAL NOT NULL DEFAULT 0.0,
                    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
                    avg_holding_minutes REAL NOT NULL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'probe',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    metric_presence_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_decision_links (
                    link_id TEXT PRIMARY KEY,
                    selection_run_id TEXT NOT NULL,
                    manager_run_id TEXT NOT NULL DEFAULT '',
                    decision_scope TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    block_id TEXT NOT NULL DEFAULT '',
                    venue TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    prompt_mode TEXT NOT NULL DEFAULT '',
                    selected_pages_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    linked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_selection_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    link_id TEXT NOT NULL,
                    selection_run_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    decision_scope TEXT NOT NULL,
                    venue TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    block_id TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    outcome_kind TEXT NOT NULL,
                    outcome_status TEXT NOT NULL,
                    pnl_value REAL NOT NULL DEFAULT 0.0,
                    pnl_currency TEXT NOT NULL DEFAULT '',
                    return_pct REAL NOT NULL DEFAULT 0.0,
                    mfe_pct REAL NOT NULL DEFAULT 0.0,
                    mae_pct REAL NOT NULL DEFAULT 0.0,
                    holding_minutes REAL NOT NULL DEFAULT 0.0,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_page_effectiveness (
                    page_id TEXT NOT NULL,
                    decision_scope TEXT NOT NULL,
                    venue TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL NOT NULL DEFAULT 0.0,
                    expectancy REAL NOT NULL DEFAULT 0.0,
                    avg_return_pct REAL NOT NULL DEFAULT 0.0,
                    median_mae_pct REAL NOT NULL DEFAULT 0.0,
                    drawdown_pressure REAL NOT NULL DEFAULT 0.0,
                    helpful_score REAL NOT NULL DEFAULT 0.0,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'probe',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    metric_presence_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (page_id, decision_scope, venue, horizon)
                );
                CREATE TABLE IF NOT EXISTS wiki_mode_recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    decision_scope TEXT NOT NULL,
                    venue TEXT NOT NULL DEFAULT '',
                    page_type TEXT NOT NULL DEFAULT '',
                    horizon TEXT NOT NULL DEFAULT '',
                    recommended_mode TEXT NOT NULL,
                    current_mode TEXT NOT NULL DEFAULT '',
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    metric_presence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            selection_columns = self._table_columns(conn, "wiki_selection_runs")
            if "budget_report_json" not in selection_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_selection_runs
                    ADD COLUMN budget_report_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            playbook_metric_columns = self._table_columns(conn, "wiki_playbook_metrics")
            if "metric_presence_json" not in playbook_metric_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_playbook_metrics
                    ADD COLUMN metric_presence_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            page_effectiveness_columns = self._table_columns(
                conn,
                "wiki_page_effectiveness",
            )
            if "metric_presence_json" not in page_effectiveness_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_page_effectiveness
                    ADD COLUMN metric_presence_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            mode_recommendation_columns = self._table_columns(
                conn,
                "wiki_mode_recommendations",
            )
            if "metric_presence_json" not in mode_recommendation_columns:
                conn.execute(
                    """
                    ALTER TABLE wiki_mode_recommendations
                    ADD COLUMN metric_presence_json TEXT NOT NULL DEFAULT '{}'
                    """
                )

    def _write_static_file(self, name: str, content: str) -> None:
        path = self.config.root_path / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    def _sections_for(self, page_type: str) -> list[str]:
        sections = list(WIKI_SECTION_ORDER)
        for section in PAGE_TYPE_EXTRA_SECTIONS.get(page_type, []):
            if section not in sections:
                sections.append(section)
        return sections

    def _select_context_pages(
        self,
        *,
        target_scope: str,
        symbols: set[str],
        page_types: set[str],
        limit: int,
    ) -> list[sqlite3.Row]:
        selected: list[sqlite3.Row] = []
        seen_page_ids: set[str] = set()
        stale_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=14)
        ).isoformat()
        if target_scope and symbols:
            direct_page_types = page_types or {"symbol"}
            direct_page_ids = [
                self.page_id(scope=target_scope, page_type=page_type, key=symbol)
                for page_type in sorted(direct_page_types)
                for symbol in sorted(symbols)
            ]
            if direct_page_ids:
                placeholders = ",".join(["?"] * len(direct_page_ids))
                with self._connect() as conn:
                    direct_rows = conn.execute(
                        f"""
                        SELECT *
                        FROM wiki_pages
                        WHERE status = 'active' AND page_id IN ({placeholders})
                        ORDER BY
                            CASE
                                WHEN freshness IN (
                                    'fresh', 'current', 'recent', 'live',
                                    'up_to_date'
                                )
                                AND updated_at >= ? THEN 0
                                WHEN freshness IN (
                                    'stale', 'old', 'expired', 'outdated'
                                )
                                OR updated_at < ? THEN 2
                                ELSE 1
                            END,
                            confidence DESC,
                            updated_at DESC
                        """,
                        (*direct_page_ids, stale_cutoff, stale_cutoff),
                    ).fetchall()
                direct_rows = sorted(
                    direct_rows,
                    key=lambda row: self._context_page_sort_key(
                        row,
                        target_scope=target_scope,
                    ),
                )
                for row in direct_rows:
                    if self._is_stale_page(row):
                        continue
                    selected.append(row)
                    seen_page_ids.add(str(row["page_id"]))
                    if len(selected) >= limit:
                        return selected

        clauses = ["status = 'active'"]
        params: list[Any] = []
        if target_scope:
            clauses.append("scope IN (?, 'core')")
            params.append(target_scope)
        if page_types:
            placeholders = ",".join(["?"] * len(page_types))
            clauses.append(f"page_type IN ({placeholders})")
            params.extend(sorted(page_types))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM wiki_pages
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE WHEN scope = ? THEN 0 WHEN scope = 'core' THEN 1 ELSE 2 END,
                    CASE
                        WHEN freshness IN (
                            'fresh', 'current', 'recent', 'live', 'up_to_date'
                        )
                        AND updated_at >= ? THEN 0
                        WHEN freshness IN (
                            'stale', 'old', 'expired', 'outdated'
                        )
                        OR updated_at < ? THEN 2
                        ELSE 1
                    END,
                    confidence DESC,
                    updated_at DESC
                LIMIT 200
                """,
                (*params, target_scope, stale_cutoff, stale_cutoff),
            ).fetchall()
        rows = sorted(
            rows,
            key=lambda row: self._context_page_sort_key(
                row,
                target_scope=target_scope,
            ),
        )
        for row in rows:
            page_id = str(row["page_id"])
            if page_id in seen_page_ids:
                continue
            row_symbols = {
                _normalize_symbol(symbol)
                for symbol in self._parse_json(
                    row["symbols_json"],
                    [],
                    field=f"wiki_pages.symbols_json:{page_id}",
                )
                if str(symbol).strip()
            }
            if symbols and row["scope"] != "core" and not (row_symbols & symbols):
                continue
            selected.append(row)
            seen_page_ids.add(page_id)
            if len(selected) >= limit:
                break
        return selected

    def _extract_context_summary(self, page_id: str) -> str:
        summary = self._summary_text(page_id)
        if not summary:
            return ""
        page = self.read_page(page_id)
        content = str(page.get("content") or "")
        title = page_id
        for line in content.splitlines():
            if line.startswith("# "):
                title = line.removeprefix("# ").strip() or page_id
                break
        return f"### {page_id} - {title}\n{summary}"

    def _summary_text(self, page_id: str) -> str:
        page = self.read_page(page_id)
        content = str(page.get("content") or "")
        marker = "## Next Context Pack Summary"
        if marker in content:
            summary = content.split(marker, 1)[1].strip()
            return summary.split("\n## ", 1)[0].strip()
        return content[:1000].strip()

    def _parse_json(
        self,
        raw: str | None,
        fallback: Any,
        *,
        field: str = "json",
        allow_missing: bool = False,
    ) -> Any:
        if raw in (None, ""):
            if allow_missing:
                return fallback
            raise JueWikiDataIntegrityError(
                f"invalid jue wiki json: {field}: missing value"
            )
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise JueWikiDataIntegrityError(
                f"invalid jue wiki json: {field}: {exc}"
            ) from exc

    def _safe_json_list(self, raw: Any, *, field: str) -> list[Any]:
        if isinstance(raw, list):
            return raw
        if raw in (None, ""):
            return []
        try:
            value = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise JueWikiSourceReadError(
                f"invalid source json list: {field}: {exc}"
            ) from exc
        return value if isinstance(value, list) else []

    def _parse_source_json_object(self, raw: Any, *, field: str) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if raw in (None, ""):
            return {}
        try:
            value = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise JueWikiSourceReadError(
                f"invalid source json object: {field}: {exc}"
            ) from exc
        return value if isinstance(value, dict) else {}

    def _parse_source_json_any(
        self,
        raw: Any,
        *,
        fallback: Any,
        field: str,
    ) -> Any:
        if raw in (None, ""):
            return fallback
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(str(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise JueWikiSourceReadError(
                f"invalid source json value: {field}: {exc}"
            ) from exc

    def _agents_md(self) -> str:
        return (
            "# Jue Wiki Runtime Guide\n\n"
            "Use wiki pages as compiled knowledge. Use source refs for audit.\n"
        )

    def _schema_md(self) -> str:
        return (
            "# Jue Wiki Schema\n\n"
            "Pages require frontmatter and fixed sections.\n"
            "\nRequired sections:\n"
            + "\n".join(f"- {section}" for section in WIKI_SECTION_ORDER)
            + "\n"
        )
