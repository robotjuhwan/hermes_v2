from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx

from tradecraft.runtime.state_store import utc_now_iso
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig


def _is_six_digit_symbol(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"\d{6}", text))


def _clean_company_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if _is_six_digit_symbol(text):
        return ""
    return text[:80]


def _to_text(raw: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(text: str) -> str:
    match = re.search(r"(\d{4})[./-](\d{2})[./-](\d{2})", text)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _parse_symbol(text: str) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return match.group(1) if match else ""


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
    max_detail_pages: int = 150
    request_delay_sec: float = 1.8
    llm_bridge_command: str = ""
    llm_bridge_args: str = ""
    llm_bridge_url: str = ""
    llm_bridge_token: str = ""
    llm_bridge_timeout_ms: int = 60000
    llm_model: str = "gpt-5.3-codex"


class NaverReportRepository:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

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
                "CREATE INDEX IF NOT EXISTS idx_reports_symbol_date ON reports(symbol, published_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_directory (
                    symbol TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    market TEXT NOT NULL DEFAULT '',
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
                symbol=symbol,
                company_name=company_name,
                market="",
                source="naver_reports",
                confidence=0.8,
                status="active",
                verified_at=now,
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
    ) -> None:
        code = str(symbol or "").strip()
        name = _clean_company_name(company_name)
        if not _is_six_digit_symbol(code) or not name:
            return
        now = str(verified_at or utc_now_iso())
        conn.execute(
            """
            INSERT INTO symbol_directory(
                symbol,
                company_name,
                market,
                status,
                source,
                confidence,
                first_seen_at,
                updated_at,
                last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name=CASE
                    WHEN excluded.company_name <> '' THEN excluded.company_name
                    ELSE symbol_directory.company_name
                END,
                market=CASE
                    WHEN excluded.market <> '' THEN excluded.market
                    ELSE symbol_directory.market
                END,
                status=CASE
                    WHEN excluded.status <> '' THEN excluded.status
                    ELSE symbol_directory.status
                END,
                source=CASE
                    WHEN excluded.source <> '' THEN excluded.source
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
                str(market or "").strip()[:24],
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
            )

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

    def get_symbol_name(self, symbol: str) -> str:
        return str(
            self.resolve_symbol_names([symbol]).get(str(symbol or "").strip()) or ""
        )

    def refresh_symbol_directory_from_krx(self, as_of: str = "") -> dict[str, Any]:
        as_of_date = str(as_of or date.today().isoformat())
        now = utc_now_iso()
        rows: list[tuple[str, str, str]] = []
        errors: list[str] = []

        try:
            from pykrx import stock  # type: ignore

            market_labels = {
                "KOSPI": "KOSPI",
                "KOSDAQ": "KOSDAQ",
                "KONEX": "KONEX",
            }
            for market, label in market_labels.items():
                try:
                    tickers = list(
                        stock.get_market_ticker_list(as_of_date, market=market)
                    )
                except Exception as exc:
                    errors.append(f"{market}: {exc}")
                    continue
                for ticker in tickers:
                    code = str(ticker or "").strip()
                    if not _is_six_digit_symbol(code):
                        continue
                    try:
                        name = _clean_company_name(stock.get_market_ticker_name(code))
                    except Exception:
                        name = ""
                    if name:
                        rows.append((code, name, label))

            for getter, label in (
                ("get_etf_ticker_list", "ETF"),
                ("get_etn_ticker_list", "ETN"),
            ):
                fn = getattr(stock, getter, None)
                if fn is None:
                    continue
                try:
                    tickers = list(fn(as_of_date))
                except Exception as exc:
                    errors.append(f"{label}: {exc}")
                    continue
                for ticker in tickers:
                    code = str(ticker or "").strip()
                    if not _is_six_digit_symbol(code):
                        continue
                    try:
                        name = _clean_company_name(stock.get_market_ticker_name(code))
                    except Exception:
                        name = ""
                    if name:
                        rows.append((code, name, label))
        except Exception as exc:
            return {
                "ok": False,
                "reason": "pykrx_unavailable",
                "detail": str(exc),
                "updated": 0,
            }

        merged: dict[str, tuple[str, str]] = {}
        for code, name, market in rows:
            if not _is_six_digit_symbol(code) or not name:
                continue
            merged[code] = (name, market)

        if not merged:
            return {
                "ok": False,
                "reason": "empty_snapshot",
                "detail": "; ".join(errors)[:300],
                "updated": 0,
            }

        with self._connect() as conn:
            for code, (name, market) in merged.items():
                self._upsert_symbol_directory_with_conn(
                    conn=conn,
                    symbol=code,
                    company_name=name,
                    market=market,
                    source="pykrx",
                    confidence=1.0,
                    status="active",
                    verified_at=now,
                )

        return {
            "ok": True,
            "updated": len(merged),
            "errors": errors[:12],
            "as_of": as_of_date,
            "updated_at": now,
        }

    def upsert_report_facts(self, report_id: int, facts: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._upsert_report_facts_with_conn(
                conn=conn,
                report_id=report_id,
                facts=facts,
            )

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
            category_counts = {
                str(item["category"] or "unknown"): int(item["cnt"] or 0)
                for item in cat_rows
            }
            return {
                "total_reports": int(row["total_reports"] or 0),
                "last_updated_at": str(row["last_updated_at"] or ""),
                "last_published_at": str(row["last_published_at"] or ""),
                "category_counts": category_counts,
                "total_symbols": int(symbol_row["cnt"] or 0),
                "symbol_last_updated_at": str(symbol_row["last_updated_at"] or ""),
                "db_path": str(self.path),
            }

    def search(
        self, query: str, symbol: str = "", category: str = "", limit: int = 10
    ) -> list[dict[str, Any]]:
        q = str(query or "").strip()
        sym = str(symbol or "").strip()
        cat = str(category or "").strip()
        max_rows = max(min(int(limit), 100), 1)
        where: list[str] = []
        params: list[Any] = []
        if sym:
            where.append("r.symbol = ?")
            params.append(sym)
        if cat:
            where.append("r.category = ?")
            params.append(cat)
        if q:
            like = f"%{q}%"
            where.append("(r.title LIKE ? OR r.content LIKE ? OR c.content LIKE ?)")
            params.extend([like, like, like])

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
              COALESCE(MAX(c.content), r.content) AS snippet
            FROM reports r
            LEFT JOIN report_chunks c ON c.report_id = r.report_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY r.report_id ORDER BY r.published_at DESC, r.updated_at DESC LIMIT ?"
        params.append(max_rows)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                snippet = str(row["snippet"] or "")
                if len(snippet) > 480:
                    snippet = snippet[:480]
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
                        "snippet": snippet,
                    }
                )
            return out

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

    def list_chunks_for_rag(self, limit: int = 50000) -> list[dict[str, Any]]:
        max_rows = max(min(int(limit), 200000), 1)
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
              c.content
            FROM reports r
            JOIN report_chunks c ON c.report_id = r.report_id
            ORDER BY r.published_at DESC, r.updated_at DESC, c.chunk_index ASC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (max_rows,)).fetchall()
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
        self._llm_bridge = LLMBridge(
            LLMBridgeConfig(
                command=config.llm_bridge_command,
                args=config.llm_bridge_args,
                url=config.llm_bridge_url,
                token=config.llm_bridge_token,
                timeout_ms=config.llm_bridge_timeout_ms,
                model=config.llm_model,
            )
        )

    async def crawl_once(self) -> dict[str, Any]:
        discovered = 0
        inserted = 0
        skipped = 0
        errors = 0
        pdf_seen: set[str] = set()

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
                            continue

                        if not _is_research_detail_url(link_url):
                            continue
                        if link_url in detail_pages_seen:
                            continue
                        detail_pages_seen.add(link_url)
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

        return {
            "status": "ok",
            "updated_at": utc_now_iso(),
            "discovered": discovered,
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors,
            "repository": self.repository.status(),
        }

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
                ok = await self._ingest_pdf(
                    client=client,
                    category=str(item.get("category") or "unknown"),
                    source_url=str(item.get("source_url") or ""),
                    detail_url=str(item.get("detail_url") or ""),
                    pdf_url=str(item.get("pdf_url") or ""),
                    title=str(item.get("title") or ""),
                    broker=str(item.get("broker") or ""),
                    symbol=str(item.get("symbol") or ""),
                    published_at=str(item.get("published_at") or ""),
                    detail_html="",
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

        raw_pages = self._extract_pdf_pages(binary)
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

        chunk_rows = _build_chunk_rows(
            page_rows,
            chunk_size=self.config.chunk_size,
            max_chunks=self.config.max_chunks_per_report,
        )

        normalized_title = title.strip() or self._title_from_url(pdf_url)
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            normalized_symbol = _parse_symbol(normalized_title)
        normalized_company_name = self._extract_company_name(
            detail_html, normalized_title
        )
        normalized_broker = broker.strip()
        normalized_analyst = self._extract_analyst(detail_html)
        normalized_date = published_at.strip()
        if not normalized_date:
            normalized_date = _parse_date(detail_html)
        if not normalized_date:
            normalized_date = utc_now_iso()[:10]
        normalized_crawled_at = utc_now_iso()

        structured_facts = _extract_basic_structured(
            text,
            page_rows,
            title=normalized_title,
            broker=normalized_broker,
            symbol=normalized_symbol,
        )
        structured_facts = await self._refine_structured_facts_via_bridge(
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

    async def _refine_structured_facts_via_bridge(
        self,
        *,
        facts: dict[str, Any],
        text: str,
    ) -> dict[str, Any]:
        bridge = self._llm_bridge
        if not bridge.ready:
            return facts

        payload = {
            "model": bridge.resolved_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
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
            result = await bridge.complete(payload)
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
    def _extract_company_name(detail_html: str, fallback_title: str) -> str:
        text = _to_text(detail_html)
        if text:
            match = re.search(r"([가-힣A-Za-z0-9]+)\s*(?:\(\d{6}\))", text)
            if match:
                return str(match.group(1) or "").strip()[:80]
        title = str(fallback_title or "").strip()
        if not title:
            return ""
        return re.sub(r"\s+", " ", title).strip()[:80]

    @staticmethod
    def _extract_analyst(detail_html: str) -> str:
        text = _to_text(detail_html)
        if not text:
            return ""
        match = re.search(r"애널리스트\s*[:：]?\s*([가-힣A-Za-z]{2,20})", text)
        if match:
            return str(match.group(1) or "").strip()[:40]
        match = re.search(r"Analyst\s*[:：]?\s*([A-Za-z .]{3,40})", text)
        if match:
            return str(match.group(1) or "").strip()[:40]
        return ""

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
            return values
        single = self.config.seed_url.strip()
        return [single] if single else []

    @staticmethod
    def _extract_title(html: str) -> str:
        match = re.search(r"<title[^>]*>([\s\S]*?)</title>", html, flags=re.IGNORECASE)
        if not match:
            return ""
        return _to_text(match.group(1) or "")

    @staticmethod
    def _extract_broker(html: str) -> str:
        text = _to_text(html)
        match = re.search(r"([가-힣A-Za-z0-9]+증권)", text)
        if not match:
            return ""
        return match.group(1)

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
