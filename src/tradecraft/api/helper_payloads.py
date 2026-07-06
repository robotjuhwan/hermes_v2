from __future__ import annotations

import json
import re
from typing import Any


def format_citation(broker: str, published_at: str, page: str) -> str:
    b = str(broker or "-").strip() or "-"
    p = str(published_at or "-").strip() or "-"
    pg = str(page or "?").strip() or "?"
    return f"[{b}, {p}, p.{pg}]"


def clean_helper_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(int(limit), 1)]


def clean_report_title(row: dict[str, Any]) -> str:
    title = clean_helper_text(row.get("title"), limit=120)
    if title and "리포트 보기" not in title:
        return title
    symbol = str(row.get("symbol") or "").strip()
    category = str(row.get("category") or "report").strip() or "report"
    return f"{symbol or 'Naver'} {category}"


def safe_helper_limit(value: Any) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = 8
    return max(min(raw, 12), 1)


def helper_query_keywords(query: str) -> list[str]:
    stopwords = {
        "최근",
        "리포트",
        "보고서",
        "긍정",
        "부정",
        "근거",
        "리스크",
        "위험",
        "정리",
        "알려줘",
        "설명",
        "요약",
        "투자",
        "전망",
        "후보",
    }
    keywords: list[str] = []
    for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", str(query or "")):
        if token in stopwords or token.isdigit():
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= 5:
            break
    return keywords


def helper_report_sort_key(
    row: dict[str, Any],
    *,
    query: str,
    symbol: str,
) -> tuple[int, int, int]:
    category = str(row.get("category") or "")
    category_score = {
        "company_analysis": 0,
        "industry_analysis": 6,
        "invest_info": 10,
        "market_info": 14,
        "economy_analysis": 16,
        "bond_analysis": 18,
    }.get(category, 20)
    row_symbol = str(row.get("symbol") or "").strip()
    score = category_score
    if symbol and row_symbol == symbol:
        score -= 3
    elif symbol:
        score += 8
    if symbol and category == "market_info":
        score += 8

    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "company_name", "snippet", "_sort_text", "broker")
    )
    compact_text = re.sub(r"\s+", "", text)
    for keyword in helper_query_keywords(query):
        if keyword and keyword in text:
            score -= 2
        if symbol and f"{keyword}({symbol})" in compact_text:
            score -= 25

    published_digits = re.sub(r"\D", "", str(row.get("published_at") or ""))
    published_rank = int(published_digits or "0")
    report_id = int(row.get("report_id") or 0)
    return (score, -published_rank, -report_id)


def helper_report_has_exact_symbol_match(
    row: dict[str, Any],
    *,
    query: str,
    symbol: str,
) -> bool:
    if not symbol:
        return False
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "company_name", "snippet", "_sort_text")
    )
    compact_text = re.sub(r"\s+", "", text)
    return any(
        f"{keyword}({symbol})" in compact_text
        for keyword in helper_query_keywords(query)
    )


def build_helper_source_draft_answer(
    *,
    query: str,
    facts_rows: list[dict[str, Any]],
    rag_rows: list[dict[str, Any]],
    strategy_context: dict[str, Any] | None = None,
) -> str:
    summary_lines: list[str] = []
    evidence_lines: list[str] = []
    risk_lines: list[str] = []
    strategy_lines: list[str] = []
    rating_counts: dict[str, int] = {}
    target_values: list[int] = []

    for item in list((strategy_context or {}).get("candidates") or [])[:5]:
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or symbol or "후보").strip()
        score = int(item.get("score") or 0)
        stance = str(item.get("stance") or "watch")
        reasons = [
            clean_helper_text(value, limit=90)
            for value in list(item.get("reasons") or [])[:2]
            if clean_helper_text(value, limit=90)
        ]
        risks = [
            clean_helper_text(value, limit=70)
            for value in list(item.get("risks") or [])[:1]
            if clean_helper_text(value, limit=70)
        ]
        reason_text = "; ".join(reasons) if reasons else "근거 추가 확인 필요"
        risk_text = f" / 리스크: {risks[0]}" if risks else ""
        strategy_lines.append(
            f"- {name}({symbol}) score {score}, {stance}: {reason_text}{risk_text}"
        )

    for row in facts_rows:
        snippet = clean_helper_text(row.get("snippet"), limit=180)
        if snippet and len(summary_lines) < 3:
            summary_lines.append(f"- {snippet}")

        facts = row.get("facts")
        if not isinstance(facts, dict):
            continue
        rating = str(facts.get("rating") or "UNKNOWN")
        rating_counts[rating] = rating_counts.get(rating, 0) + 1
        target = facts.get("target_price")
        if isinstance(target, dict):
            value = int(target.get("value") or 0)
            if value > 0:
                target_values.append(value)
        for bullet in list(facts.get("summary_bullets") or [])[:1]:
            quote_text = clean_helper_text(bullet, limit=160)
            if not quote_text:
                continue
            evidence_quote = list(facts.get("evidence_quotes") or [])
            page = str((evidence_quote[0] if evidence_quote else {}).get("page") or "?")
            evidence_lines.append(
                f"- {quote_text} "
                f"{format_citation(str(row.get('broker') or ''), str(row.get('published_at') or ''), page)}"
            )
        for risk in list(facts.get("risks") or [])[:2]:
            risk_text = clean_helper_text(risk, limit=160)
            if risk_text:
                risk_lines.append(f"- {risk_text}")

    for row in rag_rows[:3]:
        content = clean_helper_text(row.get("content"), limit=160)
        if content:
            evidence_lines.append(
                "- "
                f"{content} "
                f"{format_citation(str(row.get('broker') or ''), str(row.get('published_at') or ''), str(row.get('page_start') or '?'))}"
            )

    if not summary_lines and strategy_lines:
        summary_lines = [
            "- 전략 후보 엔진의 교차 신호를 우선 요약합니다.",
            "- 후보는 다음 거래일 블록 설계와 실행 검증을 위한 감시 리스트입니다.",
            "- 시초가 갭, 거래대금, 섹터 수급, 리포트 리스크를 함께 확인해야 합니다.",
        ]

    while len(summary_lines) < 3:
        summary_lines.append("- 리포트에 명시 근거 없음/추가 자료 필요")

    consensus_line = "- 리포트에 명시 근거 없음/추가 자료 필요"
    if rating_counts:
        top_rating = sorted(
            rating_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )[0][0]
        consensus_line = f"- 투자의견 다수: {top_rating}"
        if target_values:
            avg_target = int(round(sum(target_values) / len(target_values)))
            consensus_line = (
                f"- 투자의견 다수: {top_rating}, 목표주가 평균: {avg_target:,} KRW"
            )

    if not evidence_lines:
        evidence_lines = ["- 리포트/RAG에 명시 근거 없음"]
    if not risk_lines:
        risk_lines = ["- 명시 리스크 부족. 원문 리포트와 최신 공시/시황 교차 확인 필요"]

    return "\n".join(
        [
            f"질문: {query}",
            "",
            "요약(3줄)",
            *summary_lines[:3],
            "",
            "전략 후보/감시 리스트",
            *(strategy_lines[:5] or ["- 전략 후보 엔진에 충분한 교차 신호 없음"]),
            "",
            "핵심 근거(인용 포함)",
            *evidence_lines[:6],
            "",
            "리포트 간 차이/컨센서스",
            consensus_line,
            "",
            "리스크/반론 체크리스트",
            *risk_lines[:5],
            "",
            "근거 부족 시 안내",
            "- 리포트/RAG/전략 후보에서 직접 확인되지 않은 부분은 추가 수집 대상과 데이터 갭으로 남깁니다.",
            "",
            "운영 전제",
            "- 수집 리포트와 RAG 문단을 실거래 판단에 연결하되, 주문은 블록 규칙과 안전 게이트 검증 후 실행됩니다.",
        ]
    )


def parse_helper_llm_content(content: str) -> dict[str, Any] | None:
    text = str(content or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"answer_md": text}
    return parsed if isinstance(parsed, dict) else None


def normalize_helper_answer_contract(answer: str) -> str:
    replacements = {
        "요약": "요약(3줄)",
        "핵심 근거": "핵심 근거(인용 포함)",
        "컨센서스": "리포트 간 차이/컨센서스",
        "리스크/반론": "리스크/반론 체크리스트",
        "안내": "근거 부족 시 안내",
    }
    out: list[str] = []
    for raw_line in str(answer or "").splitlines():
        stripped = raw_line.strip()
        out.append(replacements.get(stripped, raw_line))
    return "\n".join(out).strip()
