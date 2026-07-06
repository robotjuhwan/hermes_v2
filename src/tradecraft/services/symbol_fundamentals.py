from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def jue_wiki_repair_target_symbols(
    db_path: str | Path,
    *,
    limit: int = 80,
) -> list[str]:
    path = Path(db_path)
    if not path.exists():
        return []
    max_items = max(int(limit), 0)
    if max_items <= 0:
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'wiki_repair_actions'
            """
        ).fetchone()
        if table_exists is None:
            return []
        rows = conn.execute(
            """
            SELECT page_id, action_type, status, details_json, created_at, action_id
            FROM wiki_repair_actions
            WHERE status IN ('scheduled', 'unresolved')
              AND action_type IN (
                  'refresh_symbol_fundamentals',
                  'refresh_symbol_financials',
                  'refresh_symbol_quote'
              )
            ORDER BY created_at DESC, action_id DESC
            LIMIT ?
            """,
            (max_items * 4,),
        ).fetchall()
    symbols: list[str] = []
    for row in rows:
        details = json.loads(str(row["details_json"] or "{}"))
        raw_symbols = details.get("symbols") if isinstance(details, dict) else []
        if isinstance(raw_symbols, list):
            symbols.extend(str(item or "").strip() for item in raw_symbols)
        page_id = str(row["page_id"] or "")
        page_symbol = page_id.rsplit(".", 1)[-1] if "." in page_id else page_id
        symbols.append(page_symbol)
    return [
        symbol
        for symbol in dict.fromkeys(symbols)
        if _is_symbol(symbol)
    ][:max_items]


def merge_fundamental_target_symbols(
    *,
    watchlist: list[str],
    repair_targets: list[str],
    discovered: list[str],
    limit: int = 80,
) -> list[str]:
    max_items = max(int(limit), 0)
    if max_items <= 0:
        return []
    symbols = [*watchlist, *repair_targets, *discovered]
    return [
        symbol
        for symbol in dict.fromkeys(str(item or "").strip() for item in symbols)
        if _is_symbol(symbol)
    ][:max_items]


def resolve_jue_wiki_fundamental_repair_actions(
    db_path: str | Path,
    *,
    latest_by_symbol: dict[str, dict[str, Any]],
    resolved_at: str | None = None,
) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {
            "status": "missing",
            "resolved_count": 0,
            "checked_count": 0,
            "resolved_action_ids": [],
        }
    now = resolved_at or _now_iso()
    latest = {
        str(symbol or "").strip(): payload
        for symbol, payload in latest_by_symbol.items()
        if _is_symbol(symbol) and isinstance(payload, dict)
    }
    if not latest:
        return {
            "status": "ok",
            "resolved_count": 0,
            "checked_count": 0,
            "resolved_action_ids": [],
        }
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        table_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'wiki_repair_actions'
            """
        ).fetchone()
        if table_exists is None:
            return {
                "status": "missing_table",
                "resolved_count": 0,
                "checked_count": 0,
                "resolved_action_ids": [],
            }
        rows = conn.execute(
            """
            SELECT action_id, page_id, action_type, details_json
            FROM wiki_repair_actions
            WHERE status IN ('scheduled', 'unresolved')
              AND action_type IN (
                  'refresh_symbol_fundamentals',
                  'refresh_symbol_financials',
                  'refresh_symbol_quote'
              )
            ORDER BY created_at DESC, action_id DESC
            """
        ).fetchall()
        checked_count = 0
        resolved_ids: list[str] = []
        for row in rows:
            symbols = _repair_action_symbols(row)
            matched_symbol = next((symbol for symbol in symbols if symbol in latest), "")
            if not matched_symbol:
                continue
            checked_count += 1
            payload = latest[matched_symbol]
            if not _fundamental_repair_action_is_resolved(
                action_type=str(row["action_type"] or ""),
                latest=payload,
            ):
                continue
            details = json.loads(str(row["details_json"] or "{}"))
            if not isinstance(details, dict):
                details = {}
            details["resolved_by"] = "symbol_fundamentals_collect"
            details["resolved_symbol"] = matched_symbol
            details["resolved_at"] = now
            conn.execute(
                """
                UPDATE wiki_repair_actions
                SET status = 'resolved',
                    finished_at = ?,
                    details_json = ?
                WHERE action_id = ?
                """,
                (
                    now,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    str(row["action_id"]),
                ),
            )
            resolved_ids.append(str(row["action_id"]))
    return {
        "status": "ok",
        "resolved_count": len(resolved_ids),
        "checked_count": checked_count,
        "resolved_action_ids": resolved_ids,
    }


def _repair_action_symbols(row: sqlite3.Row) -> list[str]:
    symbols: list[str] = []
    details = json.loads(str(row["details_json"] or "{}"))
    raw_symbols = details.get("symbols") if isinstance(details, dict) else []
    if isinstance(raw_symbols, list):
        symbols.extend(str(item or "").strip() for item in raw_symbols)
    page_id = str(row["page_id"] or "")
    page_symbol = page_id.rsplit(".", 1)[-1] if "." in page_id else page_id
    symbols.append(page_symbol)
    return [
        symbol
        for symbol in dict.fromkeys(symbols)
        if _is_symbol(symbol)
    ]


def _fundamental_repair_action_is_resolved(
    *,
    action_type: str,
    latest: dict[str, Any],
) -> bool:
    if str(latest.get("status") or "") != "ok":
        return False
    valuation = latest.get("valuation") if isinstance(latest.get("valuation"), dict) else {}
    financials = latest.get("financials") if isinstance(latest.get("financials"), list) else []
    if action_type == "refresh_symbol_quote":
        return valuation.get("price") is not None
    if action_type == "refresh_symbol_financials":
        return any(
            isinstance(row, dict)
            and any(
                row.get(key) is not None
                for key in (
                    "revenue",
                    "operating_profit",
                    "net_income",
                    "roe",
                    "debt_ratio",
                    "operating_margin",
                )
            )
            for row in financials
        )
    if action_type == "refresh_symbol_fundamentals":
        return any(
            valuation.get(key) is not None
            for key in (
                "price",
                "market_cap_krw",
                "per",
                "eps",
                "pbr",
                "bps",
                "industry_per",
            )
        )
    return False


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


def _safe_float(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text.upper() in {"N/A", "NA", "NULL", "NONE", "-"}:
        return None
    text = text.replace(",", "").replace("%", "").replace("배", "").replace("원", "")
    text = re.sub(r"[^0-9.+-]", "", text)
    if text in {"", "+", "-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    return int(round(parsed))


def _decode_response(response: httpx.Response, *, fallback: str = "utf-8") -> str:
    content_type = str(response.headers.get("content-type") or "").lower()
    if "euc-kr" in content_type or "ks_c_5601" in content_type:
        return response.content.decode("euc-kr", errors="replace")
    if "charset=utf-8" in content_type:
        return response.content.decode("utf-8", errors="replace")
    return response.content.decode(fallback, errors="replace")


def _extract_tag_text_by_id(raw_html: str, element_id: str) -> str:
    pattern = rf"<(?P<tag>[a-z0-9]+)[^>]*\bid=[\"']{re.escape(element_id)}[\"'][^>]*>(?P<body>[\s\S]*?)</(?P=tag)>"
    match = re.search(pattern, raw_html, flags=re.IGNORECASE)
    return _clean_text(match.group("body"), limit=120) if match else ""


def _extract_metric_pair_by_header(raw_html: str, header_text: str) -> tuple[float | None, float | None]:
    match = re.search(
        rf"{re.escape(header_text)}[\s\S]{{0,1800}}?<td[^>]*>(?P<body>[\s\S]*?)</td>",
        raw_html,
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    values = [
        _clean_text(item, limit=40)
        for item in re.findall(r"<em[^>]*>([\s\S]*?)</em>", match.group("body"), flags=re.IGNORECASE)
    ]
    if not values:
        return None, None
    first = _safe_float(values[0])
    second = _safe_float(values[1]) if len(values) > 1 else None
    return first, second


def _parse_market_cap_krw(value: Any) -> int | None:
    text = _clean_text(value, limit=120).replace(",", "")
    if not text:
        return None
    total = 0
    jo = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*조", text)
    if jo:
        total += int(float(jo.group(1)) * 1_000_000_000_000)
    eok = re.search(r"조\s*([0-9]+(?:\.[0-9]+)?)", text)
    if eok:
        total += int(float(eok.group(1)) * 100_000_000)
    elif "억" in text:
        number = _safe_float(text)
        if number is not None:
            total += int(number * 100_000_000)
    elif total == 0:
        number = _safe_float(text)
        if number is not None:
            total += int(number * 100_000_000)
    return total or None


def parse_naver_coinfo_html(
    raw_html: str,
    *,
    symbol: str,
    source_url: str = "",
) -> dict[str, Any]:
    name_match = re.search(r"<dd>\s*종목명\s*([^<]+)</dd>", raw_html)
    name = _clean_text(name_match.group(1), limit=60) if name_match else ""
    if not name:
        title_match = re.search(r"<title>\s*([^:<]+)", raw_html, flags=re.IGNORECASE)
        name = _clean_text(title_match.group(1), limit=60) if title_match else ""

    price_match = re.search(r"현재가\s*([0-9,]+)", _clean_text(raw_html, limit=5000))
    price = _safe_int(price_match.group(1)) if price_match else None

    per = _safe_float(_extract_tag_text_by_id(raw_html, "_cns_per"))
    eps = _safe_int(_extract_tag_text_by_id(raw_html, "_cns_eps"))
    if per is None:
        per = _safe_float(_extract_tag_text_by_id(raw_html, "_per"))
    if eps is None:
        eps = _safe_int(_extract_tag_text_by_id(raw_html, "_eps"))

    pbr, bps_float = _extract_metric_pair_by_header(raw_html, "PBR")
    bps = int(round(bps_float)) if bps_float is not None else None
    if pbr is None and price and bps and bps > 0:
        pbr = round(float(price) / float(bps), 2)
    dividend_yield = _safe_float(_extract_tag_text_by_id(raw_html, "_dvr"))

    market_cap_text = _extract_tag_text_by_id(raw_html, "_market_sum")
    market_cap_krw = _parse_market_cap_krw(f"{market_cap_text}억원") if market_cap_text else None

    industry_per = None
    industry_match = re.search(
        r"동일업종 PER[\s\S]{0,1000}?<em[^>]*>([\s\S]*?)</em>",
        raw_html,
        flags=re.IGNORECASE,
    )
    if industry_match:
        industry_per = _safe_float(_clean_text(industry_match.group(1), limit=30))

    raw_payload: dict[str, Any] = {
        "market_cap_text": market_cap_text,
    }
    industry_name = ""
    if _looks_like_etf_name(name):
        raw_payload["asset_class"] = "etf"
        industry_name = "ETF"

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "market_cap_krw": market_cap_krw,
        "per": per,
        "eps": eps,
        "pbr": pbr,
        "bps": bps,
        "dividend_yield_pct": dividend_yield,
        "industry_per": industry_per,
        "industry_name": industry_name,
        "as_of": _today_iso(),
        "source_url": source_url,
        "raw": raw_payload,
    }


def _extract_table_rows(raw_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", raw_html, flags=re.IGNORECASE):
        cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row_html, flags=re.IGNORECASE)
        clean_cells = [_clean_text(cell, limit=120) for cell in cells]
        if any(clean_cells):
            rows.append(clean_cells)
    return rows


_ETF_BRAND_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "RISE",
    "SOL",
    "PLUS",
    "KBSTAR",
    "HANARO",
    "ARIRANG",
    "TIMEFOLIO",
)


def _looks_like_etf_name(value: Any) -> bool:
    text = str(value or "").strip().upper()
    return bool(text) and text.startswith(_ETF_BRAND_PREFIXES)


def _extract_tables(raw_html: str) -> list[str]:
    tables = re.findall(r"<table[^>]*>[\s\S]*?</table>", raw_html, flags=re.IGNORECASE)
    return tables or [raw_html]


def _clean_wisereport_period(value: Any) -> str:
    text = _clean_text(value, limit=40)
    text = re.sub(r"\s+", "", text)
    if not text or "[" in text or "]" in text:
        return ""
    if re.fullmatch(r"20\d{2}(?:[./-](?:03|06|09|12))?(?:\([A-Za-z가-힣]+\))?", text):
        return text.replace(".", "/").replace("-", "/")
    return ""


def _wisereport_period_type(period: str) -> str:
    base = re.sub(r"\([^)]*\)", "", str(period or "")).strip()
    if re.fullmatch(r"20\d{2}(?:/12)?", base):
        return "annual"
    if re.fullmatch(r"20\d{2}/(?:03|06|09)", base):
        return "quarterly"
    return "mixed"


def _extract_wisereport_candidate(rows: list[list[str]], *, symbol: str) -> list[dict[str, Any]]:
    header_idx = -1
    periods: list[str] = []
    for idx, cells in enumerate(rows):
        if len(cells) < 2:
            continue
        clean_periods = [_clean_wisereport_period(cell) for cell in cells[1:9]]
        clean_periods = [item for item in clean_periods if item]
        if len(clean_periods) >= 2 or (
            len(clean_periods) >= 1
            and re.search(r"IFRS|재무|실적", str(cells[0] or ""), flags=re.IGNORECASE)
        ):
            header_idx = idx
            periods = clean_periods[:8]
            break
    if header_idx < 0 or not periods:
        return []

    metrics: dict[str, list[str]] = {}
    metric_names = [
        ("영업이익률", "operating_margin"),
        ("당기순이익", "net_income"),
        ("영업이익", "operating_profit"),
        ("부채비율", "debt_ratio"),
        ("매출액", "revenue"),
        ("ROE", "roe"),
    ]
    for cells in rows[header_idx + 1 :]:
        if len(cells) < 2:
            continue
        row_label = re.sub(r"\s+", "", cells[0])
        if "/" in row_label and "률" not in row_label:
            continue
        if any(marker in row_label for marker in ("컨센서스", "신용등급", "투자의견")):
            continue
        key = next((out_key for label, out_key in metric_names if label in row_label), "")
        if key:
            values = cells[1 : len(periods) + 1]
            if any(_safe_float(value) is not None for value in values):
                metrics[key] = values
    if not periods or not metrics:
        return []

    out: list[dict[str, Any]] = []
    for idx, period in enumerate(periods):
        if not period:
            continue
        row: dict[str, Any] = {
            "symbol": symbol,
            "period_type": _wisereport_period_type(period),
            "period": period,
            "raw": {},
        }
        has_value = False
        for key, values in metrics.items():
            value = values[idx] if idx < len(values) else ""
            parsed = _safe_float(value)
            row[key] = parsed
            row["raw"][key] = value
            if parsed is not None:
                has_value = True
        if not has_value:
            continue
        out.append(row)
    return out[:12]


def parse_wisereport_financials(raw_html: str, *, symbol: str) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    best_score = 0
    for table_html in _extract_tables(raw_html):
        rows = _extract_table_rows(table_html)
        candidate = _extract_wisereport_candidate(rows, symbol=symbol)
        if not candidate:
            continue
        metric_count = len(
            {
                key
                for row in candidate
                for key in (
                    "revenue",
                    "operating_profit",
                    "net_income",
                    "roe",
                    "debt_ratio",
                    "operating_margin",
                )
                if row.get(key) is not None
            }
        )
        score = len(candidate) + metric_count * 10
        if score > best_score:
            best = candidate
            best_score = score
    return best


def score_valuation(valuation: dict[str, Any], financials: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    raw = valuation.get("raw") if isinstance(valuation.get("raw"), dict) else {}
    if str(raw.get("asset_class") or "").lower() in {"etf", "etn"} or _looks_like_etf_name(
        valuation.get("name")
    ):
        return {
            "undervalued_score": 0,
            "overvalued_risk": 0,
            "quality_score": 0,
            "growth_score": 0,
            "relative_per_discount_pct": None,
            "pbr_roe_fit": None,
            "label": "unknown",
            "reasons": [],
            "risks": ["ETF는 기업 PER/PBR 대신 ETF 리서치 지표로 판단"],
            "scored_at": _now_iso(),
        }
    per = _safe_float(valuation.get("per"))
    pbr = _safe_float(valuation.get("pbr"))
    eps = _safe_float(valuation.get("eps"))
    bps = _safe_float(valuation.get("bps"))
    industry_per = _safe_float(valuation.get("industry_per"))
    dividend = _safe_float(valuation.get("dividend_yield_pct"))
    relative_discount = None
    if per and industry_per and industry_per > 0:
        relative_discount = round((industry_per - per) / industry_per * 100.0, 2)

    reasons: list[str] = []
    risks: list[str] = []
    undervalued = 35
    overvalued = 20
    quality = 35
    growth = 0

    if relative_discount is not None:
        undervalued += max(-20, min(35, relative_discount * 0.8))
        if relative_discount >= 20:
            reasons.append(f"업종 PER 대비 {relative_discount:.1f}% 낮음")
        elif relative_discount <= -20:
            overvalued += min(35, abs(relative_discount) * 0.8)
            risks.append(f"업종 PER 대비 {abs(relative_discount):.1f}% 높음")

    if per and per > 0:
        if per <= 10:
            undervalued += 12
            reasons.append(f"PER {per:.2f}배")
        elif per >= 35:
            overvalued += 18
            risks.append(f"PER {per:.2f}배 부담")
    if pbr and pbr > 0:
        if pbr <= 1:
            undervalued += 8
            reasons.append(f"PBR {pbr:.2f}배")
        elif pbr >= 4:
            overvalued += 14
            risks.append(f"PBR {pbr:.2f}배 부담")
    if eps and eps > 0:
        quality += 20
    elif eps is not None and eps <= 0:
        overvalued += 15
        risks.append("EPS가 0 이하")
    if bps and bps > 0:
        quality += 10
    if dividend and dividend > 0:
        quality += min(12, dividend * 3)
        reasons.append(f"배당수익률 {dividend:.2f}%")

    for row in financials or []:
        op = _safe_float(row.get("operating_profit"))
        margin = _safe_float(row.get("operating_margin"))
        roe = _safe_float(row.get("roe"))
        if op and op > 0:
            growth += 8
        if margin and margin > 0:
            quality += min(10, margin / 2)
        if roe and roe > 0:
            quality += min(16, roe)
        if growth >= 24:
            break

    if not any(value is not None for value in (per, pbr, eps, bps, industry_per, dividend)):
        label = "unknown"
        reasons = []
        risks = ["밸류에이션 원천 데이터 부족"]
        undervalued = 0
        overvalued = 0
        quality = 0
    else:
        undervalued = max(0, min(100, int(round(undervalued))))
        overvalued = max(0, min(100, int(round(overvalued))))
        quality = max(0, min(100, int(round(quality))))
        growth = max(0, min(100, int(round(growth))))
        if overvalued >= 65:
            label = "expensive"
        elif undervalued >= 65 and overvalued < 55:
            label = "undervalued"
        else:
            label = "fair"

    return {
        "undervalued_score": max(0, min(100, int(round(undervalued)))),
        "overvalued_risk": max(0, min(100, int(round(overvalued)))),
        "quality_score": max(0, min(100, int(round(quality)))),
        "growth_score": max(0, min(100, int(round(growth)))),
        "relative_per_discount_pct": relative_discount,
        "pbr_roe_fit": None,
        "label": label,
        "reasons": reasons[:5],
        "risks": risks[:5],
        "scored_at": _now_iso(),
    }


@dataclass(slots=True)
class SymbolFundamentalsConfig:
    db_path: str = ".runtime/symbol_fundamentals.db"
    timeout_sec: float = 8.0
    min_refresh_hours: int = 12
    max_symbols_per_collect: int = 80
    jue_wiki_db_path: str = ""


class SymbolFundamentalsRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS valuation_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    price INTEGER,
                    market_cap_krw INTEGER,
                    per REAL,
                    eps INTEGER,
                    pbr REAL,
                    bps INTEGER,
                    dividend_yield_pct REAL,
                    industry_per REAL,
                    industry_name TEXT NOT NULL DEFAULT '',
                    as_of TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    crawled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ok',
                    error_message TEXT NOT NULL DEFAULT '',
                    last_attempt_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(symbol, as_of)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_snapshots (
                    financial_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    period_type TEXT NOT NULL DEFAULT '',
                    period TEXT NOT NULL,
                    revenue REAL,
                    operating_profit REAL,
                    net_income REAL,
                    roe REAL,
                    debt_ratio REAL,
                    operating_margin REAL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    crawled_at TEXT NOT NULL,
                    UNIQUE(symbol, period_type, period)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS valuation_scores (
                    symbol TEXT PRIMARY KEY,
                    undervalued_score INTEGER NOT NULL DEFAULT 0,
                    overvalued_risk INTEGER NOT NULL DEFAULT 0,
                    quality_score INTEGER NOT NULL DEFAULT 0,
                    growth_score INTEGER NOT NULL DEFAULT 0,
                    relative_per_discount_pct REAL,
                    pbr_roe_fit REAL,
                    label TEXT NOT NULL DEFAULT 'unknown',
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    risks_json TEXT NOT NULL DEFAULT '[]',
                    scored_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_valuation_symbol_crawled ON valuation_snapshots(symbol, crawled_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_valuation_status ON valuation_snapshots(status, crawled_at DESC)")

    def upsert_snapshot(
        self,
        valuation: dict[str, Any],
        *,
        financials: list[dict[str, Any]] | None = None,
        score: dict[str, Any] | None = None,
    ) -> None:
        symbol = str(valuation.get("symbol") or "").strip()
        if not _is_symbol(symbol):
            raise ValueError("symbol must be a 6-digit KRX code")
        now = str(valuation.get("crawled_at") or _now_iso())
        as_of = str(valuation.get("as_of") or _today_iso())
        raw = valuation.get("raw") if isinstance(valuation.get("raw"), dict) else {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO valuation_snapshots (
                    symbol, name, price, market_cap_krw, per, eps, pbr, bps,
                    dividend_yield_pct, industry_per, industry_name, as_of,
                    source_url, raw_json, crawled_at, status, error_message,
                    last_attempt_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, as_of) DO UPDATE SET
                    name=excluded.name,
                    price=excluded.price,
                    market_cap_krw=excluded.market_cap_krw,
                    per=excluded.per,
                    eps=excluded.eps,
                    pbr=excluded.pbr,
                    bps=excluded.bps,
                    dividend_yield_pct=excluded.dividend_yield_pct,
                    industry_per=excluded.industry_per,
                    industry_name=excluded.industry_name,
                    source_url=excluded.source_url,
                    raw_json=excluded.raw_json,
                    crawled_at=excluded.crawled_at,
                    status=excluded.status,
                    error_message=excluded.error_message,
                    last_attempt_at=excluded.last_attempt_at
                """,
                (
                    symbol,
                    str(valuation.get("name") or ""),
                    valuation.get("price"),
                    valuation.get("market_cap_krw"),
                    valuation.get("per"),
                    valuation.get("eps"),
                    valuation.get("pbr"),
                    valuation.get("bps"),
                    valuation.get("dividend_yield_pct"),
                    valuation.get("industry_per"),
                    str(valuation.get("industry_name") or ""),
                    as_of,
                    str(valuation.get("source_url") or ""),
                    json.dumps(raw, ensure_ascii=False),
                    now,
                    str(valuation.get("status") or "ok"),
                    str(valuation.get("error_message") or ""),
                    str(valuation.get("last_attempt_at") or now),
                ),
            )
            for row in financials or []:
                period = str(row.get("period") or "").strip()
                if not period:
                    continue
                conn.execute(
                    """
                    INSERT INTO financial_snapshots (
                        symbol, period_type, period, revenue, operating_profit,
                        net_income, roe, debt_ratio, operating_margin, raw_json,
                        crawled_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, period_type, period) DO UPDATE SET
                        revenue=excluded.revenue,
                        operating_profit=excluded.operating_profit,
                        net_income=excluded.net_income,
                        roe=excluded.roe,
                        debt_ratio=excluded.debt_ratio,
                        operating_margin=excluded.operating_margin,
                        raw_json=excluded.raw_json,
                        crawled_at=excluded.crawled_at
                    """,
                    (
                        symbol,
                        str(row.get("period_type") or ""),
                        period,
                        row.get("revenue"),
                        row.get("operating_profit"),
                        row.get("net_income"),
                        row.get("roe"),
                        row.get("debt_ratio"),
                        row.get("operating_margin"),
                        json.dumps(row.get("raw") or {}, ensure_ascii=False),
                        now,
                    ),
                )
            resolved_score = score or score_valuation(valuation, financials or [])
            conn.execute(
                """
                INSERT INTO valuation_scores (
                    symbol, undervalued_score, overvalued_risk, quality_score,
                    growth_score, relative_per_discount_pct, pbr_roe_fit, label,
                    reasons_json, risks_json, scored_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    undervalued_score=excluded.undervalued_score,
                    overvalued_risk=excluded.overvalued_risk,
                    quality_score=excluded.quality_score,
                    growth_score=excluded.growth_score,
                    relative_per_discount_pct=excluded.relative_per_discount_pct,
                    pbr_roe_fit=excluded.pbr_roe_fit,
                    label=excluded.label,
                    reasons_json=excluded.reasons_json,
                    risks_json=excluded.risks_json,
                    scored_at=excluded.scored_at
                """,
                (
                    symbol,
                    int(resolved_score.get("undervalued_score") or 0),
                    int(resolved_score.get("overvalued_risk") or 0),
                    int(resolved_score.get("quality_score") or 0),
                    int(resolved_score.get("growth_score") or 0),
                    resolved_score.get("relative_per_discount_pct"),
                    resolved_score.get("pbr_roe_fit"),
                    str(resolved_score.get("label") or "unknown"),
                    json.dumps(list(resolved_score.get("reasons") or []), ensure_ascii=False),
                    json.dumps(list(resolved_score.get("risks") or []), ensure_ascii=False),
                    str(resolved_score.get("scored_at") or _now_iso()),
                ),
            )

    def record_error(self, symbol: str, message: str) -> None:
        now = _now_iso()
        self.upsert_snapshot(
            {
                "symbol": symbol,
                "as_of": _today_iso(),
                "crawled_at": now,
                "last_attempt_at": now,
                "status": "error",
                "error_message": str(message)[:300],
            },
            score={
                "undervalued_score": 0,
                "overvalued_risk": 0,
                "quality_score": 0,
                "growth_score": 0,
                "relative_per_discount_pct": None,
                "pbr_roe_fit": None,
                "label": "unknown",
                "reasons": [],
                "risks": [str(message)[:120]],
                "scored_at": now,
            },
        )

    def latest(self, symbol: str) -> dict[str, Any] | None:
        code = str(symbol or "").strip()
        if not _is_symbol(code):
            return None
        with self._connect() as conn:
            valuation = conn.execute(
                """
                SELECT * FROM valuation_snapshots
                WHERE symbol = ?
                ORDER BY crawled_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
            if valuation is None:
                return None
            score = conn.execute(
                "SELECT * FROM valuation_scores WHERE symbol = ?",
                (code,),
            ).fetchone()
            financials = conn.execute(
                """
                SELECT * FROM financial_snapshots
                WHERE symbol = ?
                ORDER BY period DESC
                LIMIT 8
                """,
                (code,),
            ).fetchall()
        return self._compose_latest(valuation, score, financials)

    def _compose_latest(
        self,
        valuation: sqlite3.Row,
        score: sqlite3.Row | None,
        financials: list[sqlite3.Row],
    ) -> dict[str, Any]:
        raw_json = json.loads(str(valuation["raw_json"] or "{}"))
        score_payload = {
            "undervalued_score": int(score["undervalued_score"] or 0) if score else 0,
            "overvalued_risk": int(score["overvalued_risk"] or 0) if score else 0,
            "quality_score": int(score["quality_score"] or 0) if score else 0,
            "growth_score": int(score["growth_score"] or 0) if score else 0,
            "relative_per_discount_pct": score["relative_per_discount_pct"] if score else None,
            "pbr_roe_fit": score["pbr_roe_fit"] if score else None,
            "label": str(score["label"] or "unknown") if score else "unknown",
            "reasons": json.loads(str(score["reasons_json"] or "[]")) if score else [],
            "risks": json.loads(str(score["risks_json"] or "[]")) if score else [],
            "scored_at": str(score["scored_at"] or "") if score else "",
        }
        return {
            "status": str(valuation["status"] or "unknown"),
            "symbol": str(valuation["symbol"] or ""),
            "name": str(valuation["name"] or ""),
            "valuation": {
                "name": str(valuation["name"] or ""),
                "price": valuation["price"],
                "market_cap_krw": valuation["market_cap_krw"],
                "per": valuation["per"],
                "eps": valuation["eps"],
                "pbr": valuation["pbr"],
                "bps": valuation["bps"],
                "dividend_yield_pct": valuation["dividend_yield_pct"],
                "industry_per": valuation["industry_per"],
                "industry_name": str(valuation["industry_name"] or ""),
                "as_of": str(valuation["as_of"] or ""),
                "source_url": str(valuation["source_url"] or ""),
                "crawled_at": str(valuation["crawled_at"] or ""),
                "raw": raw_json,
            },
            "score": score_payload,
            "financials": [
                {
                    "period_type": str(row["period_type"] or ""),
                    "period": str(row["period"] or ""),
                    "revenue": row["revenue"],
                    "operating_profit": row["operating_profit"],
                    "net_income": row["net_income"],
                    "roe": row["roe"],
                    "debt_ratio": row["debt_ratio"],
                    "operating_margin": row["operating_margin"],
                }
                for row in financials
            ],
            "error_message": str(valuation["error_message"] or ""),
            "last_attempt_at": str(valuation["last_attempt_at"] or ""),
        }

    def is_fresh(self, symbol: str, *, min_refresh_hours: int) -> bool:
        latest = self.latest(symbol)
        if not latest:
            return False
        crawled_at = str((latest.get("valuation") or {}).get("crawled_at") or "")
        try:
            parsed = datetime.fromisoformat(crawled_at)
        except ValueError:
            return False
        return datetime.now(timezone.utc) - parsed <= timedelta(hours=max(min_refresh_hours, 1))

    def status(self, *, min_refresh_hours: int | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       MAX(crawled_at) AS latest_crawled_at,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
                FROM valuation_snapshots
                """
            ).fetchone()
            latest_rows = conn.execute(
                """
                SELECT *
                FROM valuation_snapshots
                ORDER BY symbol ASC, crawled_at DESC, snapshot_id DESC
                """
            ).fetchall()
        latest_by_symbol: dict[str, sqlite3.Row] = {}
        for item in latest_rows:
            symbol = str(item["symbol"] or "").strip()
            if symbol and symbol not in latest_by_symbol:
                latest_by_symbol[symbol] = item

        stale_count = 0
        fresh_symbol_count = 0
        ok_count = 0
        error_symbol_count = 0
        symbol_rows: list[tuple[datetime, dict[str, Any]]] = []
        refresh_hours = max(int(min_refresh_hours or 0), 0)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=refresh_hours)
            if refresh_hours > 0
            else None
        )
        for symbol, item in latest_by_symbol.items():
            status = str(item["status"] or "unknown")
            if status == "ok":
                ok_count += 1
            elif status == "error":
                error_symbol_count += 1
            crawled_at = str(item["crawled_at"] or "")
            stale = False
            if cutoff is not None:
                try:
                    parsed = datetime.fromisoformat(crawled_at)
                    stale = parsed < cutoff
                except ValueError:
                    stale = True
            if stale:
                stale_count += 1
            if status == "ok" and not stale:
                fresh_symbol_count += 1
            try:
                sort_at = datetime.fromisoformat(crawled_at)
            except ValueError:
                sort_at = datetime.min.replace(tzinfo=timezone.utc)
            symbol_rows.append(
                (
                    sort_at,
                    {
                        "symbol": symbol,
                        "name": str(item["name"] or ""),
                        "status": status,
                        "crawled_at": crawled_at,
                        "stale": stale,
                    },
                )
            )
        latest_symbols = [
            item
            for _, item in sorted(
                symbol_rows,
                key=lambda row: row[0],
                reverse=True,
            )[:8]
        ]
        latest_symbols_count = len(latest_symbols)
        latest_symbols_stale_count = sum(1 for item in latest_symbols if item["stale"])
        latest_symbols_fresh_count = sum(
            1
            for item in latest_symbols
            if item["status"] == "ok" and not item["stale"]
        )
        total_symbols = len(latest_by_symbol)
        return {
            "status": "ok",
            "db_path": self.db_path,
            "total_snapshots": int(row["total"] or 0),
            "total_symbols": total_symbols,
            "ok_symbol_count": ok_count,
            "error_symbol_count": error_symbol_count,
            "stale_symbol_count": stale_count,
            "fresh_symbol_count": fresh_symbol_count,
            "stale_ratio": (stale_count / total_symbols) if total_symbols else 0.0,
            "latest_symbols_count": latest_symbols_count,
            "latest_symbols_fresh_count": latest_symbols_fresh_count,
            "latest_symbols_stale_count": latest_symbols_stale_count,
            "latest_symbols_stale_ratio": (
                latest_symbols_stale_count / latest_symbols_count
                if latest_symbols_count
                else 0.0
            ),
            "error_count": int(row["errors"] or 0),
            "latest_crawled_at": str(row["latest_crawled_at"] or ""),
            "latest_symbols": latest_symbols,
        }


class SymbolFundamentalsService:
    def __init__(self, config: SymbolFundamentalsConfig) -> None:
        self.config = config
        self.repository = SymbolFundamentalsRepository(config.db_path)

    def latest(self, symbol: str) -> dict[str, Any] | None:
        return self.repository.latest(symbol)

    def status(self) -> dict[str, Any]:
        return self.repository.status(
            min_refresh_hours=self.config.min_refresh_hours,
        )

    async def collect_symbols(
        self,
        symbols: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        unique_symbols = [
            symbol
            for symbol in dict.fromkeys(str(item or "").strip() for item in symbols)
            if _is_symbol(symbol)
        ][: max(int(self.config.max_symbols_per_collect), 1)]
        summary = {
            "status": "ok",
            "requested": len(symbols),
            "target_count": len(unique_symbols),
            "collected": 0,
            "skipped": 0,
            "errors": [],
            "items": [],
            "updated_at": _now_iso(),
        }
        collected_latest: dict[str, dict[str, Any]] = {}
        timeout = httpx.Timeout(float(self.config.timeout_sec))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for symbol in unique_symbols:
                if not force and self.repository.is_fresh(
                    symbol,
                    min_refresh_hours=self.config.min_refresh_hours,
                ):
                    latest = self.repository.latest(symbol) or {}
                    summary["skipped"] += 1
                    summary["items"].append(
                        {
                            "symbol": symbol,
                            "status": "skipped",
                            "reason": "fresh",
                            "latest": latest,
                        }
                    )
                    continue
                try:
                    item = await self._collect_one(client, symbol)
                except Exception as exc:
                    self.repository.record_error(symbol, str(exc))
                    summary["errors"].append({"symbol": symbol, "error": str(exc)[:180]})
                    summary["items"].append(
                        {"symbol": symbol, "status": "error", "error": str(exc)[:180]}
                    )
                    continue
                summary["collected"] += 1
                summary["items"].append({"symbol": symbol, "status": "ok", "latest": item})
                collected_latest[symbol] = item
        if self.config.jue_wiki_db_path and collected_latest:
            summary["jue_wiki_repair_resolution"] = (
                resolve_jue_wiki_fundamental_repair_actions(
                    self.config.jue_wiki_db_path,
                    latest_by_symbol=collected_latest,
                )
            )
        if summary["errors"] and summary["collected"] == 0:
            summary["status"] = "error"
        elif summary["errors"]:
            summary["status"] = "partial"
        return summary

    async def _collect_one(self, client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
        source_url = f"https://finance.naver.com/item/coinfo.naver?code={symbol}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = await client.get(source_url, headers=headers)
        response.raise_for_status()
        coinfo_html = _decode_response(response, fallback="euc-kr")
        valuation = parse_naver_coinfo_html(coinfo_html, symbol=symbol, source_url=source_url)
        valuation["status"] = "ok"
        valuation["crawled_at"] = _now_iso()
        valuation["last_attempt_at"] = valuation["crawled_at"]

        financials: list[dict[str, Any]] = []
        iframe = re.search(
            r'<iframe[^>]+id=["\']coinfo_cp["\'][^>]+src=["\']([^"\']+)["\']',
            coinfo_html,
            flags=re.IGNORECASE,
        )
        if iframe:
            iframe_url = html.unescape(iframe.group(1))
            iframe_response = await client.get(
                iframe_url,
                headers={**headers, "Referer": source_url},
            )
            iframe_response.raise_for_status()
            wisereport_html = _decode_response(iframe_response)
            financials = parse_wisereport_financials(wisereport_html, symbol=symbol)
            valuation["raw"]["wisereport_url"] = iframe_url
            valuation["raw"]["financial_rows"] = len(financials)

        score = score_valuation(valuation, financials)
        self.repository.upsert_snapshot(valuation, financials=financials, score=score)
        return self.repository.latest(symbol) or {"symbol": symbol, "status": "ok"}
