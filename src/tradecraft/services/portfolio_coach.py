from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import wrap
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from tradecraft.runtime.state_store import utc_now_iso
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig

KST = ZoneInfo("Asia/Seoul")


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


def _safe_int(value: Any) -> int:
    return int(round(_safe_float(value)))


def _sha256_json(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso_date(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10:
        return raw[:10]
    return raw


def _clip(text: Any, limit: int) -> str:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return ""
    return raw[: max(limit, 1)]


def _to_kst_date_time(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        now_kst = datetime.now(KST)
        return now_kst.strftime("%Y-%m-%d"), now_kst.strftime("%H:%M")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        now_kst = datetime.now(KST)
        return now_kst.strftime("%Y-%m-%d"), now_kst.strftime("%H:%M")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    kst_dt = parsed.astimezone(KST)
    return kst_dt.strftime("%Y-%m-%d"), kst_dt.strftime("%H:%M")


def _parse_date_value(text: str) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw + "T00:00:00+09:00")
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _wrap_soft(line: str, limit: int = 140) -> list[str]:
    raw = str(line or "")
    if len(raw) <= limit:
        return [raw]
    leading = len(raw) - len(raw.lstrip(" "))
    indent = " " * leading
    stripped = raw.strip()
    if not stripped:
        return [raw]
    if raw.lstrip().startswith("|"):
        return [raw[: max(limit - 1, 1)] + "…"]
    wrapped = wrap(
        stripped,
        width=max(min(limit - leading, 130), 40),
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not wrapped:
        return [raw[: max(limit - 1, 1)] + "…"]
    out: list[str] = []
    for idx, item in enumerate(wrapped):
        prefix = indent
        if idx > 0 and stripped.startswith("- "):
            prefix = indent + "  "
        out.append(prefix + item)
    return out


def _clean_line(text: Any, limit: int = 120) -> str:
    return _clip(text, limit)


def _name_code(name: str, ticker: str) -> str:
    n = _clean_line(name, 40)
    t = _clean_line(ticker, 12)
    if n and t:
        if n == t:
            if len(t) == 6 and t.isdigit():
                return f"미상종목({t})"
            return t
        return f"{n}({t})"
    return n or t or "-"


@dataclass(slots=True)
class RebalancePlannerConfig:
    target_cash_weight: float = 0.10
    max_single_weight: float = 0.20
    target_positions: int = 6
    max_trades_per_message: int = 6
    min_trade_krw: float = 50000.0


class RebalancePlanner:
    def __init__(self, config: RebalancePlannerConfig) -> None:
        self.config = config

    def _normalized_target_weights(
        self,
        ideas: list[dict[str, Any]],
    ) -> dict[str, float]:
        max_positions = max(int(self.config.target_positions), 1)
        selected = ideas[:max_positions]
        investable = max(1.0 - float(self.config.target_cash_weight), 0.0)
        if not selected or investable <= 0:
            return {}

        base_scores: dict[str, float] = {}
        for row in selected:
            ticker = str(row.get("ticker") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            score = _safe_float(row.get("base_score"))
            if score <= 0:
                score = max(_safe_float(row.get("upside_pct")) / 10.0, 0.1)
            base_scores[ticker] = max(score, 0.1)

        if not base_scores:
            return {}

        total_score = sum(base_scores.values())
        raw = {
            ticker: (score / total_score) * investable
            for ticker, score in base_scores.items()
        }
        cap = max(min(float(self.config.max_single_weight), 0.9), 0.05)
        clipped = dict(raw)

        for _ in range(8):
            overflow = 0.0
            eligible: list[str] = []
            for ticker, weight in clipped.items():
                if weight > cap:
                    overflow += weight - cap
                    clipped[ticker] = cap
                else:
                    eligible.append(ticker)
            if overflow <= 1e-9 or not eligible:
                break
            eligible_sum = sum(base_scores[ticker] for ticker in eligible)
            if eligible_sum <= 0:
                break
            for ticker in eligible:
                clipped[ticker] += overflow * (base_scores[ticker] / eligible_sum)

        total_clipped = sum(clipped.values())
        if total_clipped > 0:
            scale = investable / total_clipped
            clipped = {
                ticker: max(min(weight * scale, cap), 0.0)
                for ticker, weight in clipped.items()
            }

        remainder = investable - sum(clipped.values())
        if remainder > 1e-6:
            growable = [
                ticker for ticker, weight in clipped.items() if weight < cap - 1e-6
            ]
            while remainder > 1e-6 and growable:
                add_each = remainder / float(len(growable))
                next_growable: list[str] = []
                for ticker in growable:
                    room = max(cap - clipped[ticker], 0.0)
                    add = min(room, add_each)
                    clipped[ticker] += add
                    remainder -= add
                    if clipped[ticker] < cap - 1e-6:
                        next_growable.append(ticker)
                growable = next_growable

        return {ticker: round(weight, 4) for ticker, weight in clipped.items()}

    def plan(
        self,
        *,
        snapshot: dict[str, Any],
        enriched: list[dict[str, Any]],
        ideas: list[dict[str, Any]],
        resolve_name: Callable[..., str],
        data_ref: str,
        as_of: str,
    ) -> dict[str, Any]:
        total_value = _safe_float(snapshot.get("cash")) + sum(
            _safe_float(row.get("market_value")) for row in enriched
        )
        if total_value <= 0:
            total_value = 1.0

        current_alloc: dict[str, float] = {}
        price_map: dict[str, float] = {}
        evidence_map: dict[str, list[str]] = {}
        for row in enriched:
            ticker = str(row.get("ticker") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            current_alloc[ticker] = _safe_float(row.get("weight"))
            price_map[ticker] = _safe_float(row.get("last_price"))
            evidence_map[ticker] = [data_ref]

        for row in ideas:
            ticker = str(row.get("ticker") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            lp = _safe_float(row.get("last_price"))
            if lp > 0:
                price_map[ticker] = lp
            evidence = [
                _clean_line(item, 70)
                for item in list(row.get("evidence") or [])
                if _clean_line(item, 70)
            ]
            evidence = list(dict.fromkeys(evidence + [data_ref]))
            evidence_map[ticker] = evidence[:2]

        target_alloc = self._normalized_target_weights(ideas)
        all_tickers = sorted(set(current_alloc.keys()) | set(target_alloc.keys()))
        table_rows: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        residual_krw = 0.0

        for ticker in all_tickers:
            current_weight = _safe_float(current_alloc.get(ticker))
            target_weight = _safe_float(target_alloc.get(ticker))
            delta_weight = target_weight - current_weight
            est_krw_signed = delta_weight * total_value
            est_krw = abs(est_krw_signed)
            price = max(_safe_float(price_map.get(ticker)), 0.0)
            est_shares = int(round(est_krw / price)) if price > 0 else 0
            if est_shares == 0 and est_krw > 0 and price > 0:
                est_shares = 1

            if abs(delta_weight) < 0.0005:
                action = "HOLD"
            elif delta_weight > 0:
                action = "BUY"
            else:
                action = (
                    "REDUCE"
                    if current_weight > float(self.config.max_single_weight)
                    else "SELL"
                )

            label_name = resolve_name(ticker, "")
            table_rows.append(
                {
                    "name": label_name,
                    "ticker": ticker,
                    "current_weight": round(current_weight, 4),
                    "target_weight": round(target_weight, 4),
                    "delta_weight": round(delta_weight, 4),
                    "est_krw": int(round(est_krw_signed)),
                    "est_shares": int(est_shares),
                    "action": action,
                }
            )

            if action == "HOLD":
                continue
            if est_krw < float(self.config.min_trade_krw):
                residual_krw += est_krw
                continue

            evidence = list(evidence_map.get(ticker) or [data_ref])[:2]
            reason = f"목표 {target_weight*100:.1f}% vs 현재 {current_weight*100:.1f}% (Δ {delta_weight*100:+.1f}%p)"
            priority = (
                0
                if (
                    action == "REDUCE"
                    and current_weight > float(self.config.max_single_weight)
                )
                else 1
            )
            trades.append(
                {
                    "ticker": ticker,
                    "name": label_name,
                    "action": action,
                    "priority": priority,
                    "abs_delta_weight": abs(delta_weight),
                    "est_krw": int(round(est_krw)),
                    "est_shares": int(est_shares),
                    "target_weight": round(target_weight, 4),
                    "current_weight": round(current_weight, 4),
                    "reason": reason,
                    "evidence": evidence,
                }
            )

        trades.sort(
            key=lambda row: (
                int(row.get("priority") or 9),
                -_safe_float(row.get("abs_delta_weight")),
            )
        )
        trades = trades[: max(int(self.config.max_trades_per_message), 1)]

        notes = [
            "수수료/세금 추정 미반영",
            "수량 추정은 1주 단위 반올림",
            f"최소 트레이드 금액 필터: {int(round(float(self.config.min_trade_krw))):,}원",
            f"잔여 미체결 추정: {int(round(residual_krw)):,}원",
            f"기준시각(as_of): {as_of}",
        ]

        return {
            "target_allocation": target_alloc,
            "current_allocation": current_alloc,
            "trades": trades,
            "rebalance_table_rows": table_rows,
            "notes": notes,
        }


@dataclass(slots=True)
class SecurityResolver:
    resolver: Callable[..., str]

    def resolve(self, ticker: str, *candidates: str) -> str:
        return str(self.resolver(ticker, *candidates) or "")

    def format(self, ticker: str, *candidates: str) -> str:
        resolved = self.resolve(ticker, *candidates)
        return _name_code(resolved, ticker)


def render_portfolio_coach_md_v3(data: dict[str, Any]) -> str:
    header_raw = data.get("header")
    header: dict[str, Any] = dict(header_raw) if isinstance(header_raw, dict) else {}
    mode = _clean_line(header.get("mode"), 24) or "PAPER_DIRECT"
    date_kst = _clean_line(header.get("date_kst"), 10)
    time_kst = _clean_line(header.get("time_kst"), 5)
    if not date_kst or not time_kst:
        now_kst = datetime.now(KST)
        date_kst = now_kst.strftime("%Y-%m-%d")
        time_kst = now_kst.strftime("%H:%M")

    action_plan_raw = data.get("action_plan")
    action_plan: dict[str, Any] = (
        dict(action_plan_raw) if isinstance(action_plan_raw, dict) else {}
    )
    actions = [
        row
        for row in list(action_plan.get("trades") or data.get("actions") or [])
        if isinstance(row, dict)
    ]
    if not actions:
        actions = [
            row for row in list(data.get("trades") or []) if isinstance(row, dict)
        ]
    model_port_raw = data.get("model_portfolio")
    model_portfolio: dict[str, Any] = (
        dict(model_port_raw) if isinstance(model_port_raw, dict) else {}
    )
    model_targets = [
        row
        for row in list(model_portfolio.get("targets") or [])
        if isinstance(row, dict)
    ]
    ideas = [row for row in list(data.get("ideas") or []) if isinstance(row, dict)]
    portfolio_raw = data.get("portfolio")
    portfolio: dict[str, Any] = (
        dict(portfolio_raw) if isinstance(portfolio_raw, dict) else {}
    )
    coverage_v2_raw = data.get("evidence_coverage")
    evidence_coverage: dict[str, Any] = (
        dict(coverage_v2_raw) if isinstance(coverage_v2_raw, dict) else {}
    )
    status_raw = data.get("data_status")
    data_status: dict[str, Any] = (
        dict(status_raw) if isinstance(status_raw, dict) else {}
    )
    market_mood = [
        _clean_line(row, 110)
        for row in list(data.get("market_mood") or [])
        if _clean_line(row, 110)
    ]
    if len(market_mood) < 3:
        fallback_lines = [
            f"데이터 기준시각(as_of): {date_kst} {time_kst} KST",
            "시장 분위기: 데이터 제한으로 보수적 대응 권고",
            "확정 수치 부족 시 신규 진입보다 리스크 점검 우선",
        ]
        for row in fallback_lines:
            if len(market_mood) >= 3:
                break
            market_mood.append(row)
    if len(market_mood) > 6:
        market_mood = market_mood[:6]

    coverage_raw = data.get("coverage")
    coverage: dict[str, Any] = (
        dict(coverage_raw) if isinstance(coverage_raw, dict) else {}
    )

    lines: list[str] = []
    lines.append(f"[Portfolio Coach] {mode} {date_kst} {time_kst} (KST)")
    lines.append("")
    lines.append("## 시장 분위기")
    for row in market_mood:
        lines.append(f"- {row}")
    lines.append("")

    lines.append("## 내 포트폴리오 스냅샷")
    total_text = (
        _clean_line(portfolio.get("total_krw") or portfolio.get("total"), 40) or "-"
    )
    cash_text = (
        _clean_line(portfolio.get("cash_krw") or portfolio.get("cash"), 40) or "-"
    )
    concentration = (
        _clean_line(
            portfolio.get("concentration_summary") or portfolio.get("concentration"), 80
        )
        or "-"
    )
    lines.append(f"- 총 평가금액: {total_text} / 현금: {cash_text}")
    lines.append(f"- 집중도: {concentration}")
    position_rows = [
        row for row in list(portfolio.get("positions") or []) if isinstance(row, dict)
    ]
    for row in position_rows[:6]:
        ticker = _clean_line(row.get("ticker"), 12)
        name = _clean_line(row.get("name"), 40)
        weight = _safe_float(row.get("weight"))
        value_krw = _safe_int(row.get("market_value"))
        lines.append(
            f"- 보유: {_name_code(name, ticker)} / 비중 {weight*100:.1f}% / 평가액 {value_krw:,}원"
        )
    lines.append("")

    lines.append("## 오늘의 실행안")
    for idx in range(3):
        trade = actions[idx] if idx < len(actions) else {}
        action = (
            _clean_line(trade.get("type"), 12).upper()
            or _clean_line(trade.get("action"), 12).upper()
            or "HOLD"
        )
        status = _clean_line(trade.get("status"), 12).upper() or "PROPOSED"
        ticker = _clean_line(trade.get("ticker"), 12)
        name = _clean_line(trade.get("name"), 40)
        label = _name_code(name, ticker)
        size_raw_obj = trade.get("size")
        size_raw: dict[str, Any] = (
            dict(size_raw_obj) if isinstance(size_raw_obj, dict) else {}
        )
        size_kind = _clean_line(size_raw.get("kind"), 16) or "SHARES"
        size_val = size_raw.get("value")
        if isinstance(size_val, float):
            size_text = f"{size_val:.2f}"
        else:
            size_text = str(size_val if size_val is not None else "-")

        rationale = [
            _clean_line(row, 100)
            for row in list(trade.get("rationale_bullets") or [])
            if _clean_line(row, 100)
        ][:2]
        key_numbers = [
            _clean_line(row, 90)
            for row in list(trade.get("key_numbers") or [])
            if _clean_line(row, 90)
        ][:2]
        risks = [
            _clean_line(row, 90)
            for row in list(trade.get("risks") or [])
            if _clean_line(row, 90)
        ][:2]
        invalidation = [
            _clean_line(row, 90)
            for row in list(trade.get("invalidation") or [])
            if _clean_line(row, 90)
        ][:2]
        evidence = [
            _clean_line(row, 70)
            for row in list(trade.get("evidence") or [])
            if _clean_line(row, 70)
        ][:2]

        status_suffix = " [제안 보류]" if status == "ON_HOLD" else ""
        lines.append(
            f"{idx+1}) [{action}] {label} / {size_kind} {size_text}{status_suffix}"
        )
        lines.append(
            f"   - 왜 지금?: {' / '.join(rationale) if rationale else '근거 부족'}"
        )
        lines.append(
            f"   - 핵심 숫자: {' / '.join(key_numbers) if key_numbers else '근거 부족'}"
        )
        lines.append(f"   - 근거: {' / '.join(evidence) if evidence else '근거 부족'}")
        lines.append(f"   - 리스크: {' / '.join(risks) if risks else '근거 부족'}")
        lines.append(
            f"   - 무효화 조건: {' / '.join(invalidation) if invalidation else '근거 보강 시 재평가'}"
        )
    lines.append("")

    lines.append("## 장투 모델 포트폴리오 제안")
    targets = model_targets[:6] if model_targets else ideas[:6]
    if not targets:
        lines.append("- 모델 포트 구성 불가: 리서치/근거 데이터 부족")
    for idx, idea in enumerate(targets, start=1):
        ticker = _clean_line(idea.get("ticker"), 12)
        name = _clean_line(idea.get("name"), 40)
        label = _name_code(name, ticker)
        rating = _clean_line(idea.get("rating_consensus"), 16) or "UNKNOWN"
        tp = _safe_int(idea.get("tp_consensus"))
        upside = _safe_float(idea.get("upside_pct"))
        coverage_count = _safe_int(idea.get("coverage_count"))
        last_update = _clean_line(idea.get("last_update"), 10) or "-"
        bull = [
            _clean_line(row, 90)
            for row in list(idea.get("bull_points") or [])
            if _clean_line(row, 90)
        ][:2]
        bear = [
            _clean_line(row, 90)
            for row in list(idea.get("bear_points") or [])
            if _clean_line(row, 90)
        ][:2]
        watch = [
            _clean_line(row, 90)
            for row in list(idea.get("what_to_watch") or [])
            if _clean_line(row, 90)
        ][:2]
        evidence = [
            _clean_line(row, 70)
            for row in list(idea.get("evidence") or [])
            if _clean_line(row, 70)
        ][:2]

        target_weight = _safe_float(idea.get("target_weight"))
        why = _clean_line(idea.get("why"), 110)
        lines.append(f"{idx}) **{label}** / 목표비중 {target_weight*100:.1f}%")
        lines.append(
            f"   - rating {rating} / TP {tp:,}원 / upside {upside:.1f}% / coverage {coverage_count} / {last_update}"
        )
        lines.append(f"   - 편입 이유: {why or '리서치 컨센서스 기반 분산 편입'}")
        lines.append(f"   - Bull: {' / '.join(bull) if bull else '근거 부족'}")
        lines.append(f"   - Bear: {' / '.join(bear) if bear else '근거 부족'}")
        lines.append(f"   - Watch: {' / '.join(watch) if watch else '근거 부족'}")
        lines.append(f"   - 근거: {' / '.join(evidence) if evidence else '근거 부족'}")
    lines.append("")

    lines.append("## 리서치 근거/커버리지 + 다음 액션")
    rebalance_rows = [
        row
        for row in list(action_plan.get("rebalance_table_rows") or [])
        if isinstance(row, dict)
    ]
    if rebalance_rows:
        lines.append("- REBALANCE 표(현재→목표):")
        for row in rebalance_rows[:6]:
            ticker = _clean_line(row.get("ticker"), 12)
            name = _clean_line(row.get("name"), 40)
            cur = _safe_float(row.get("current_weight"))
            tgt = _safe_float(row.get("target_weight"))
            dlt = _safe_float(row.get("delta_weight"))
            act = _clean_line(row.get("action"), 10).upper() or "HOLD"
            lines.append(
                f"  - {_name_code(name, ticker)}: {cur*100:.1f}% -> {tgt*100:.1f}% (Δ {dlt*100:+.1f}%p, {act})"
            )
    lines.append(
        f"- 리포트 사용 수: {_safe_int(evidence_coverage.get('reports_used_count') or data_status.get('reports_used_count') or coverage.get('reports_covered'))} / 티커 수: {_safe_int(evidence_coverage.get('tickers_covered') or data_status.get('tickers_covered') or coverage.get('tickers_covered'))}"
    )
    rejects = [
        row
        for row in list(
            evidence_coverage.get("filter_rejects")
            or coverage.get("filter_rejects")
            or []
        )
        if isinstance(row, dict)
    ]
    if rejects:
        top = []
        for row in rejects[:3]:
            reason = _clean_line(row.get("reason"), 40) or "unknown"
            count = _safe_int(row.get("count"))
            top.append(f"{reason}({count})")
        lines.append(f"- 후보 탈락 상위 3사유: {' / '.join(top)}")

    gaps = [
        _clean_line(row, 100)
        for row in list(
            evidence_coverage.get("gaps")
            or data_status.get("gaps")
            or coverage.get("gaps")
            or []
        )
        if _clean_line(row, 100)
    ]
    for row in gaps[:4]:
        lines.append(f"- 데이터 갭: {row}")

    next_actions = [
        _clean_line(row, 100)
        for row in list(
            evidence_coverage.get("next_actions")
            or data_status.get("next_actions")
            or coverage.get("next_actions")
            or []
        )
        if _clean_line(row, 100)
    ]
    for row in next_actions[:4]:
        lines.append(f"- 다음 액션: {row}")

    notes = [
        _clean_line(row, 120)
        for row in list(data.get("notes") or [])
        if _clean_line(row, 120)
    ]
    lines.append("")
    lines.append(notes[0] if notes else "TRADING — 실제 매매는 사용자 책임")
    return "\n".join(lines).strip()


def render_portfolio_coach_markdown_v2(data: dict[str, Any]) -> str:
    return render_portfolio_coach_md_v3(data)


def lint_portfolio_coach_message(md: str) -> str:
    raw = str(md or "")
    if not raw.strip():
        return raw

    text = raw.replace("\r\n", "\n")
    text = text.replace("근거 부족 체크", "데이터 근거 보강")
    text = text.replace("Watchlist Codes", "")
    text = text.replace("Knowledge Memory", "")
    text = re.sub(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?([+-]\d{2}:\d{2}|Z)",
        "KST시간",
        text,
    )

    forbidden = [
        (r"사라", "검토"),
        (r"팔아라", "검토"),
        (r"매수하라", "검토"),
        (r"매도하라", "검토"),
        (r"비중\s*줄여라", "비중 점검"),
        (r"비중\s*늘려라", "비중 점검"),
    ]
    for pattern, repl in forbidden:
        text = re.sub(pattern, repl, text)

    lines = text.split("\n")
    compact: list[str] = []
    for line in lines:
        current = line.rstrip()
        if current.startswith("## "):
            if compact and compact[-1] != "":
                compact.append("")
            compact.append(current)
            continue
        compact.append(current)

    stabilized: list[str] = []
    for idx, line in enumerate(compact):
        stabilized.append(line)
        if line.startswith("## "):
            has_next = idx + 1 < len(compact)
            if has_next and compact[idx + 1] != "":
                stabilized.append("")

    required = [
        "## 시장 분위기",
        "## 내 포트폴리오 스냅샷",
        "## 오늘의 실행안",
        "## 장투 모델 포트폴리오 제안",
        "## 리서치 근거/커버리지 + 다음 액션",
    ]
    joined = "\n".join(stabilized)
    for marker in required:
        if marker in joined:
            continue
        if marker.endswith("핵심 체크"):
            continue
        if not joined.endswith("\n"):
            joined += "\n"
        joined += f"\n{marker}\n\n- 근거 부족"

    final_lines: list[str] = []
    for line in joined.split("\n"):
        for row in _wrap_soft(line, limit=140):
            final_lines.append(row)

    out: list[str] = []
    prev_blank = False
    for line in final_lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        out.append(line)
        prev_blank = is_blank
    return "\n".join(out).strip()


class HoldingsProvider(Protocol):
    async def get_snapshot(self, user_id: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class PortfolioCoachConfig:
    state_db_path: str
    user_id: str
    lookback_days: int = 60
    concentration_threshold: float = 0.30
    max_candidates: int = 12
    top_n: int = 5
    option_count: int = 3
    trigger_count: int = 3
    time_horizon: str = "중기"
    max_single_position_weight: float = 0.20
    target_cash_weight: float = 0.05
    max_new_positions: int = 4
    target_positions: int = 8
    max_trades_per_message: int = 8
    min_trade_krw: float = 50000.0
    per_trade_risk_budget: float = 0.0075
    max_sector_weight: float = 0.35
    rebalance_frequency: str = "weekly"
    risk_budget: str = "중간"
    idea_filters: str = "최근 리포트 존재"
    factor_weights_json: str = ""
    ticker_name_map_json: str = ""
    direct_report_lookback_days: int = 180
    buy_min_upside: float = 0.08
    hold_min_upside: float = 0.03
    sell_downside_threshold: float = -0.07
    review_queue_enabled: bool = True
    llm_bridge_command: str = ""
    llm_bridge_args: str = ""
    llm_bridge_url: str = ""
    llm_bridge_token: str = ""
    llm_bridge_timeout_ms: int = 60000
    llm_model: str = "gpt-5.3-codex"


class KISHoldingsProvider:
    def __init__(self, kis: Any) -> None:
        self.kis = kis

    async def get_snapshot(self, user_id: str) -> dict[str, Any]:
        assets = await self.kis.fetch_balance_assets()
        as_of = utc_now_iso()
        positions: list[dict[str, Any]] = []
        cash = 0.0
        total_position_value = 0.0

        for row in assets:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "").strip().lower()
            asset = str(row.get("asset") or "").strip()
            if kind == "cash" and asset == "KRW":
                cash += max(_safe_float(row.get("value_krw") or row.get("qty")), 0.0)
                continue
            if kind != "position":
                continue
            ticker = str(row.get("asset") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            quantity = _safe_float(row.get("qty"))
            market_value = max(_safe_float(row.get("value_krw")), 0.0)
            avg_price = _safe_float(row.get("avg_price"))
            position = {
                "ticker": ticker,
                "name": str(row.get("asset_name") or ticker),
                "quantity": quantity,
                "avg_price": avg_price if avg_price > 0 else None,
                "cost_basis": (avg_price * quantity)
                if avg_price > 0 and quantity > 0
                else None,
                "market_value": market_value if market_value > 0 else None,
                "weight": None,
            }
            positions.append(position)
            total_position_value += market_value

        total_asset_value = total_position_value + max(cash, 0.0)
        denominator = total_asset_value if total_asset_value > 0 else 1.0
        for row in positions:
            value = _safe_float(row.get("market_value"))
            row["weight"] = value / denominator if denominator > 0 else 0.0

        return {
            "user_id": user_id,
            "as_of": as_of,
            "positions": positions,
            "cash": cash,
        }


class PortfolioCoachStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    as_of TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    holdings_raw_json TEXT NOT NULL,
                    holdings_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS advice_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    as_of TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message_md TEXT NOT NULL,
                    used_candidates_json TEXT NOT NULL,
                    holdings_hash TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    message_hash TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_advice_dedupe ON advice_messages(as_of_date, user_id, holdings_hash, candidate_hash)"
            )
            self._ensure_column(conn, "advice_messages", "reviewed_at", "TEXT")
            self._ensure_column(conn, "advice_messages", "review_note", "TEXT")
            self._ensure_column(
                conn,
                "advice_messages",
                "rebalance_targets_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        column_type: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {str(row[1]) for row in rows}
        if column in columns:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def write_snapshot(self, snapshot: dict[str, Any], holdings_hash: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_snapshots(as_of, user_id, holdings_raw_json, holdings_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.get("as_of") or now),
                    str(snapshot.get("user_id") or ""),
                    json.dumps(snapshot, ensure_ascii=False),
                    holdings_hash,
                    now,
                ),
            )

    def was_sent_today(
        self, as_of_date: str, user_id: str, holdings_hash: str, candidate_hash: str
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id
                FROM advice_messages
                WHERE as_of_date = ? AND user_id = ? AND holdings_hash = ? AND candidate_hash = ? AND status = 'sent'
                ORDER BY message_id DESC
                LIMIT 1
                """,
                (as_of_date, user_id, holdings_hash, candidate_hash),
            ).fetchone()
            return row is not None

    def has_existing_message_today(
        self,
        as_of_date: str,
        user_id: str,
        holdings_hash: str,
        candidate_hash: str,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id
                FROM advice_messages
                WHERE as_of_date = ? AND user_id = ? AND holdings_hash = ? AND candidate_hash = ?
                  AND status IN ('pending_review', 'sent')
                ORDER BY message_id DESC
                LIMIT 1
                """,
                (as_of_date, user_id, holdings_hash, candidate_hash),
            ).fetchone()
            return row is not None

    def write_advice_message(
        self,
        *,
        as_of: str,
        user_id: str,
        message_md: str,
        used_candidates: list[dict[str, Any]],
        holdings_hash: str,
        candidate_hash: str,
        status: str,
        review_note: str = "",
        rebalance_targets: list[dict[str, Any]] | None = None,
    ) -> int:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO advice_messages(
                    as_of, as_of_date, user_id, message_md, used_candidates_json,
                    holdings_hash, candidate_hash, message_hash, sent_at, status,
                    reviewed_at, review_note, rebalance_targets_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    as_of,
                    _iso_date(as_of),
                    user_id,
                    message_md,
                    json.dumps(used_candidates, ensure_ascii=False),
                    holdings_hash,
                    candidate_hash,
                    hashlib.sha256(message_md.encode("utf-8")).hexdigest(),
                    now,
                    status,
                    None,
                    review_note,
                    json.dumps(rebalance_targets or [], ensure_ascii=False),
                ),
            )
            rowid = cur.lastrowid
            if rowid is None:
                return 0
            return int(rowid)

    def list_advice_messages(
        self, *, status: str = "pending_review", limit: int = 50
    ) -> list[dict[str, Any]]:
        resolved_limit = max(min(int(limit), 200), 1)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, as_of, as_of_date, user_id, message_md, used_candidates_json,
                       holdings_hash, candidate_hash, message_hash, sent_at, status,
                       reviewed_at, review_note, rebalance_targets_json
                FROM advice_messages
                WHERE (? = '' OR status = ?)
                ORDER BY message_id DESC
                LIMIT ?
                """,
                (status, status, resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            try:
                payload["used_candidates"] = json.loads(
                    str(payload.get("used_candidates_json") or "[]")
                )
            except json.JSONDecodeError:
                payload["used_candidates"] = []
            try:
                payload["rebalance_targets"] = json.loads(
                    str(payload.get("rebalance_targets_json") or "[]")
                )
            except json.JSONDecodeError:
                payload["rebalance_targets"] = []
            out.append(payload)
        return out

    def get_advice_message(self, message_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, as_of, as_of_date, user_id, message_md, used_candidates_json,
                       holdings_hash, candidate_hash, message_hash, sent_at, status,
                       reviewed_at, review_note, rebalance_targets_json
                FROM advice_messages
                WHERE message_id = ?
                LIMIT 1
                """,
                (int(message_id),),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        try:
            payload["used_candidates"] = json.loads(
                str(payload.get("used_candidates_json") or "[]")
            )
        except json.JSONDecodeError:
            payload["used_candidates"] = []
        try:
            payload["rebalance_targets"] = json.loads(
                str(payload.get("rebalance_targets_json") or "[]")
            )
        except json.JSONDecodeError:
            payload["rebalance_targets"] = []
        return payload

    def list_recent_rebalance_history(
        self, *, user_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        resolved_limit = max(min(int(limit), 20), 1)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, as_of, status, rebalance_targets_json
                FROM advice_messages
                WHERE user_id = ?
                ORDER BY message_id DESC
                LIMIT ?
                """,
                (user_id, resolved_limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                targets = json.loads(str(row["rebalance_targets_json"] or "[]"))
            except json.JSONDecodeError:
                targets = []
            if not isinstance(targets, list):
                targets = []
            normalized_targets = [item for item in targets if isinstance(item, dict)]
            if not normalized_targets:
                continue
            out.append(
                {
                    "message_id": int(row["message_id"] or 0),
                    "as_of": str(row["as_of"] or ""),
                    "status": str(row["status"] or ""),
                    "targets": normalized_targets,
                }
            )
        return out

    def update_message_status(
        self,
        *,
        message_id: int,
        status: str,
        review_note: str = "",
    ) -> bool:
        now = utc_now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE advice_messages
                SET status = ?, reviewed_at = ?, review_note = ?
                WHERE message_id = ?
                """,
                (status, now, review_note, int(message_id)),
            )
            return cur.rowcount > 0


class PortfolioCoachService:
    def __init__(
        self,
        config: PortfolioCoachConfig,
        *,
        holdings_provider: HoldingsProvider,
        report_repo: Any,
        rag_store: Any,
        kis: Any,
    ) -> None:
        self.config = config
        self.holdings_provider = holdings_provider
        self.report_repo = report_repo
        self.rag_store = rag_store
        self.kis = kis
        self.store = PortfolioCoachStore(config.state_db_path)
        self.llm_bridge = LLMBridge(
            LLMBridgeConfig(
                command=config.llm_bridge_command,
                args=config.llm_bridge_args,
                url=config.llm_bridge_url,
                token=config.llm_bridge_token,
                timeout_ms=config.llm_bridge_timeout_ms,
                model=config.llm_model,
            )
        )
        self._name_cache: dict[str, str] = {}
        self._ticker_name_map: dict[str, str] = {}
        raw_map = str(config.ticker_name_map_json or "").strip()
        if raw_map:
            try:
                parsed_map = json.loads(raw_map)
            except json.JSONDecodeError:
                parsed_map = {}
            if isinstance(parsed_map, dict):
                for raw_key, raw_value in parsed_map.items():
                    code = str(raw_key or "").strip()
                    label = _clean_line(raw_value, 40)
                    if len(code) == 6 and code.isdigit() and label:
                        self._ticker_name_map[code] = label
        self.security_resolver = SecurityResolver(self._resolve_security_name)

    async def build_advice(self) -> dict[str, Any]:
        try:
            snapshot = await self.holdings_provider.get_snapshot(self.config.user_id)
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"잔고 불러오기 실패: {exc}",
                "message": "잔고 불러오기 실패로 조언 생성을 건너뜁니다.",
                "used_candidates": [],
                "holdings_hash": "",
                "candidate_hash": "",
                "as_of": utc_now_iso(),
            }

        holdings_hash = _sha256_json(snapshot)
        self.store.write_snapshot(snapshot, holdings_hash=holdings_hash)

        positions = list(snapshot.get("positions") or [])
        if not positions:
            message = "잔고 포지션이 없어 Portfolio Coach 조언을 생성하지 않습니다."
            return {
                "status": "skipped",
                "reason": "no_positions",
                "message": message,
                "used_candidates": [],
                "holdings_hash": holdings_hash,
                "candidate_hash": "",
                "as_of": str(snapshot.get("as_of") or utc_now_iso()),
            }

        enriched = await self._enrich_positions(snapshot)
        candidates = self._build_candidates(snapshot, enriched)
        pack = await self._build_actionable_pack(
            snapshot=snapshot, enriched=enriched, candidates=candidates
        )
        candidate_hash = _sha256_json(pack.get("dedupe_key") or pack)
        as_of = str(snapshot.get("as_of") or utc_now_iso())
        as_of_date = _iso_date(as_of)

        if self.store.has_existing_message_today(
            as_of_date, self.config.user_id, holdings_hash, candidate_hash
        ):
            return {
                "status": "skipped",
                "reason": "duplicate_daily_message",
                "message": "동일 잔고/후보 조합은 오늘 이미 발송되어 스킵합니다.",
                "used_candidates": list(pack.get("used_candidates") or []),
                "holdings_hash": holdings_hash,
                "candidate_hash": candidate_hash,
                "as_of": as_of,
            }

        llm_json = await self._render_with_llm_json(snapshot=snapshot, pack=pack)
        action_payload = self._normalize_action_payload(
            llm_json if isinstance(llm_json, dict) else None,
            pack=pack,
        )
        message = lint_portfolio_coach_message(
            render_portfolio_coach_md_v3(action_payload)
        )
        response_status = "pending_review" if self.config.review_queue_enabled else "ok"
        message_id = 0
        rebalance_targets_for_history = self._extract_rebalance_targets_for_history(
            advice_seed_json=dict(pack.get("advice_seed_json") or {})
        )
        if self.config.review_queue_enabled:
            message_id = self.store.write_advice_message(
                as_of=as_of,
                user_id=self.config.user_id,
                message_md=message,
                used_candidates=list(pack.get("used_candidates") or []),
                holdings_hash=holdings_hash,
                candidate_hash=candidate_hash,
                status="pending_review",
                rebalance_targets=rebalance_targets_for_history,
            )
        return {
            "status": response_status,
            "reason": "",
            "message": message,
            "message_id": message_id,
            "used_candidates": list(pack.get("used_candidates") or []),
            "pack": pack,
            "holdings_hash": holdings_hash,
            "candidate_hash": candidate_hash,
            "as_of": as_of,
        }

    def mark_sent(self, payload: dict[str, Any], status: str) -> None:
        message_id = int(payload.get("message_id") or 0)
        if message_id > 0:
            self.store.update_message_status(message_id=message_id, status=status)
            return
        pack_obj = payload.get("pack")
        seed_obj: dict[str, Any] = {}
        if isinstance(pack_obj, dict):
            seed_candidate = pack_obj.get("advice_seed_json")
            if isinstance(seed_candidate, dict):
                seed_obj = seed_candidate
        used_candidates_obj = payload.get("used_candidates")
        used_candidates = (
            list(used_candidates_obj) if isinstance(used_candidates_obj, list) else []
        )
        self.store.write_advice_message(
            as_of=str(payload.get("as_of") or utc_now_iso()),
            user_id=self.config.user_id,
            message_md=str(payload.get("message") or ""),
            used_candidates=used_candidates,
            holdings_hash=str(payload.get("holdings_hash") or ""),
            candidate_hash=str(payload.get("candidate_hash") or ""),
            status=status,
            rebalance_targets=self._extract_rebalance_targets_for_history(
                advice_seed_json=seed_obj
            ),
        )

    async def _enrich_positions(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        as_of = str(snapshot.get("as_of") or utc_now_iso())
        date_to = _iso_date(as_of)
        base_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        date_from = (
            (base_dt - timedelta(days=max(int(self.config.lookback_days), 1)))
            .date()
            .isoformat()
        )

        for row in list(snapshot.get("positions") or []):
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            name = str(row.get("name") or ticker).strip() or ticker
            weight = _safe_float(row.get("weight"))

            quote_name = ""
            last_price = 0.0
            day_change_pct = 0.0
            if self.kis is not None:
                try:
                    quote = await self.kis.fetch_domestic_quote(ticker)
                    quote_name = str(quote.get("name") or "").strip()
                    last_price = _safe_float(quote.get("price"))
                    raw_obj = quote.get("raw")
                    raw: dict[str, Any] = raw_obj if isinstance(raw_obj, dict) else {}
                    day_change_pct = _safe_float(
                        raw.get("prdy_ctrt") or raw.get("prdy_ctrt_sign") or 0.0
                    )
                    if day_change_pct == 0.0:
                        day_change_pct = _safe_float(raw.get("stck_prdy_ctrt"))
                except Exception:
                    pass

            reports = self.report_repo.search(
                query="", symbol=ticker, category="", limit=3
            )
            report_meta: list[dict[str, Any]] = []
            report_facts: list[dict[str, Any]] = []
            for report in reports:
                report_meta.append(
                    {
                        "report_id": int(report.get("report_id") or 0),
                        "title": str(report.get("title") or ""),
                        "broker": str(report.get("broker") or ""),
                        "published_at": str(report.get("published_at") or ""),
                    }
                )
                report_id = int(report.get("report_id") or 0)
                if report_id <= 0:
                    continue
                facts = self.report_repo.get_report_facts(report_id)
                if isinstance(facts, dict):
                    facts_payload = dict(facts)
                    facts_payload["report_id"] = report_id
                    facts_payload["broker"] = str(report.get("broker") or "")
                    facts_payload["published_at"] = str(
                        report.get("published_at") or ""
                    )
                    report_facts.append(facts_payload)

            rag_rows: list[dict[str, Any]] = []
            if self.rag_store is not None and self.rag_store.available:
                try:
                    rag_rows = self.rag_store.query(
                        query=f"{name} {ticker} 리스크 목표주가",
                        symbol=ticker,
                        date_from=date_from,
                        date_to=date_to,
                        limit=3,
                    )
                except Exception:
                    rag_rows = []

            out.append(
                {
                    "ticker": ticker,
                    "name": quote_name or name,
                    "quantity": _safe_float(row.get("quantity")),
                    "avg_price": _safe_float(row.get("avg_price")),
                    "market_value": _safe_float(row.get("market_value")),
                    "weight": weight,
                    "price_as_of": as_of,
                    "last_price": last_price,
                    "day_change_pct": day_change_pct,
                    "change_5d_pct": None,
                    "volatility_20d": None,
                    "report_meta": report_meta,
                    "report_facts": report_facts,
                    "rag_rows": rag_rows,
                }
            )
        return out

    def _build_candidates(
        self, snapshot: dict[str, Any], enriched: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        sorted_by_move = sorted(
            enriched,
            key=lambda row: _safe_float(row.get("weight"))
            * abs(_safe_float(row.get("day_change_pct"))),
            reverse=True,
        )
        for row in sorted_by_move[:3]:
            ticker = str(row.get("ticker") or "")
            name = str(row.get("name") or ticker)
            weight = _safe_float(row.get("weight"))
            day_move = _safe_float(row.get("day_change_pct"))
            as_of = str(row.get("price_as_of") or "")
            citations = [f"DATA({as_of})"]
            candidates.append(
                {
                    "id": f"price-{ticker}",
                    "type": "PRICE_MOVE",
                    "ticker": ticker,
                    "name": name,
                    "title": f"가격 변동 점검: {name}",
                    "facts": [
                        f"당일 변동률 {day_move:+.2f}%",
                        f"포트 비중 {weight*100:.1f}%",
                        f"현재가 {int(_safe_float(row.get('last_price'))):,} KRW"
                        if _safe_float(row.get("last_price")) > 0
                        else "현재가 정보 제한",
                    ],
                    "why_it_matters": "비중이 있는 종목의 단기 변동은 손익 변동폭을 키움",
                    "checks": [
                        "변동 원인 뉴스/공시 확인",
                        "리포트 근거와 현재 수급 흐름 비교",
                    ],
                    "pros": ["시장 데이터 기준 관측치가 명확함"],
                    "cons": ["단기 가격 변동만으로 방향성을 단정하기 어려움"],
                    "citations": citations,
                    "score": weight * abs(day_move),
                }
            )

        for row in enriched:
            weight = _safe_float(row.get("weight"))
            if weight <= float(self.config.concentration_threshold):
                continue
            ticker = str(row.get("ticker") or "")
            name = str(row.get("name") or ticker)
            excess = max(weight - float(self.config.concentration_threshold), 0.0)
            candidates.append(
                {
                    "id": f"conc-{ticker}",
                    "type": "CONCENTRATION",
                    "ticker": ticker,
                    "name": name,
                    "title": f"집중도 점검: {name}",
                    "facts": [
                        f"종목 비중 {weight*100:.1f}%",
                        f"임계치 초과 {excess*100:.1f}%p",
                    ],
                    "why_it_matters": "단일 종목 집중은 변동성 이벤트에 취약",
                    "checks": [
                        "이익/리스크 이벤트 캘린더 점검",
                        "동일 섹터 대체 시나리오 비교",
                    ],
                    "pros": ["집중도 관리로 이벤트 리스크 노출을 점검 가능"],
                    "cons": ["집중도 축소 시 반등 구간 기회비용 가능"],
                    "citations": [f"DATA({str(row.get('price_as_of') or '')})"],
                    "score": excess,
                }
            )

        for row in enriched:
            ticker = str(row.get("ticker") or "")
            name = str(row.get("name") or ticker)
            weight = _safe_float(row.get("weight"))
            facts_rows = list(row.get("report_facts") or [])
            if len(facts_rows) >= 2:
                latest = facts_rows[0]
                prev = facts_rows[1]
                latest_target = _safe_int(
                    (latest.get("target_price") or {}).get("value")
                )
                prev_target = _safe_int((prev.get("target_price") or {}).get("value"))
                if (
                    latest_target > 0
                    and prev_target > 0
                    and latest_target != prev_target
                ):
                    change = ((latest_target - prev_target) / prev_target) * 100.0
                    broker = str(latest.get("broker") or "")
                    date = str(latest.get("published_at") or "")
                    candidates.append(
                        {
                            "id": f"delta-{ticker}",
                            "type": "REPORT_DELTA",
                            "ticker": ticker,
                            "name": name,
                            "title": f"리포트 변화: {name}",
                            "facts": [
                                f"목표주가 변화 {change:+.1f}%",
                                f"이전 {prev_target:,} -> 최신 {latest_target:,} KRW",
                            ],
                            "why_it_matters": "리포트 가정 변화는 기대수익/리스크 인식 변화를 의미",
                            "checks": [
                                "변화 사유(실적/밸류) 원문 확인",
                                "다른 증권사 컨센서스 비교",
                            ],
                            "pros": ["리포트 가정 변화가 수치로 확인됨"],
                            "cons": ["증권사별 가정이 상이할 수 있음"],
                            "citations": [f"[{broker}, {date}, p.?]"],
                            "score": weight * abs(change),
                        }
                    )

            if facts_rows:
                latest = facts_rows[0]
                risks = list(latest.get("risks") or [])
                if risks:
                    broker = str(latest.get("broker") or "")
                    date = str(latest.get("published_at") or "")
                    candidates.append(
                        {
                            "id": f"risk-{ticker}",
                            "type": "RISK_KEYWORD",
                            "ticker": ticker,
                            "name": name,
                            "title": f"리스크 키워드: {name}",
                            "facts": [str(risks[0])[:160]],
                            "why_it_matters": "최신 리포트 리스크는 단기 체크포인트 우선순위를 높임",
                            "checks": [
                                "리스크 발생 조건/트리거 확인",
                                "다음 실적/수주 이벤트 모니터링",
                            ],
                            "pros": ["최신 리스크 키워드 기반으로 우선순위 정렬 가능"],
                            "cons": ["리스크 문구의 정량화 한계"],
                            "citations": [f"[{broker}, {date}, p.?]"],
                            "score": weight * 12.0,
                        }
                    )

        for row in candidates:
            citations = [
                str(item).strip()
                for item in list(row.get("citations") or [])
                if str(item).strip()
            ]
            if not citations:
                row["citations"] = ["근거 부족"]

        candidates.sort(key=lambda row: _safe_float(row.get("score")), reverse=True)
        return candidates[: max(int(self.config.max_candidates), 3)]

    def _select_top_n(
        self, candidates: list[dict[str, Any]], top_n: int
    ) -> list[dict[str, Any]]:
        return list(candidates[: max(int(top_n), 1)])

    def _build_strategy_spec(
        self,
        *,
        target_cash_weight: float | None = None,
    ) -> dict[str, Any]:
        factor_weights: dict[str, float] = {
            "value": 0.33,
            "momentum": 0.34,
            "quality": 0.33,
        }
        raw = str(self.config.factor_weights_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                normalized: dict[str, float] = {}
                for key, value in parsed.items():
                    label = str(key).strip().lower()
                    if not label:
                        continue
                    normalized[label] = max(_safe_float(value), 0.0)
                if normalized:
                    factor_weights = normalized
        resolved_target_cash_weight = float(self.config.target_cash_weight)
        if target_cash_weight is not None:
            resolved_target_cash_weight = max(
                min(_safe_float(target_cash_weight), 0.9), 0.0
            )

        return {
            "time_horizon": self.config.time_horizon,
            "max_single_position_weight": float(self.config.max_single_position_weight),
            "target_cash_weight": round(resolved_target_cash_weight, 4),
            "max_new_positions": int(self.config.max_new_positions),
            "target_positions": int(self.config.target_positions),
            "max_trades_per_message": int(self.config.max_trades_per_message),
            "min_trade_krw": float(self.config.min_trade_krw),
            "per_trade_risk_budget": float(self.config.per_trade_risk_budget),
            "max_sector_weight": float(self.config.max_sector_weight),
            "rebalance_frequency": self.config.rebalance_frequency,
            "risk_budget": self.config.risk_budget,
            "idea_filters": self.config.idea_filters,
            "factor_weights": factor_weights,
        }

    def _decide_target_cash_weight(
        self,
        *,
        selected_ideas: list[dict[str, Any]],
        reports_covered: int,
        tickers_covered: int,
    ) -> float:
        base = max(min(float(self.config.target_cash_weight), 0.9), 0.0)
        if not selected_ideas:
            return min(max(base + 0.10, 0.03), 0.35)

        buy_like = {"BUY", "OUTPERFORM", "STRONG_BUY", "OVERWEIGHT"}
        avg_upside = sum(
            _safe_float(row.get("upside_pct")) for row in selected_ideas
        ) / float(len(selected_ideas))
        avg_coverage = sum(
            _safe_float(row.get("coverage_count")) for row in selected_ideas
        ) / float(len(selected_ideas))
        buy_like_ratio = sum(
            1
            for row in selected_ideas
            if str(row.get("rating_consensus") or "").upper() in buy_like
        ) / float(len(selected_ideas))

        adjust = 0.0
        if reports_covered < 40 or tickers_covered < 10:
            adjust += 0.08
        elif reports_covered < 80 or tickers_covered < 20:
            adjust += 0.04

        if avg_upside >= 15.0 and buy_like_ratio >= 0.65 and avg_coverage >= 2.5:
            adjust -= 0.03
        if avg_upside <= 7.0 or buy_like_ratio < 0.45:
            adjust += 0.04
        if avg_coverage < 1.5:
            adjust += 0.03

        decided = base + adjust
        return min(max(decided, 0.03), 0.35)

    def _extract_rebalance_targets_for_history(
        self, *, advice_seed_json: dict[str, Any]
    ) -> list[dict[str, Any]]:
        model = advice_seed_json.get("model_portfolio")
        if not isinstance(model, dict):
            return []
        rows: list[dict[str, Any]] = []
        for row in list(model.get("targets") or []):
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            weight = max(_safe_float(row.get("target_weight")), 0.0)
            if weight <= 0:
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "name": str(row.get("name") or ticker),
                    "target_weight": round(weight, 6),
                }
            )
        rows.sort(key=lambda item: _safe_float(item.get("target_weight")), reverse=True)
        return rows

    def _build_rebalance_history_context(
        self,
        *,
        current_targets: list[dict[str, Any]],
        current_cash_weight: float,
    ) -> tuple[list[dict[str, Any]], str]:
        history_rows = self.store.list_recent_rebalance_history(
            user_id=self.config.user_id,
            limit=5,
        )
        context_rows: list[dict[str, Any]] = []
        for row in history_rows:
            targets = [
                {
                    "ticker": str(item.get("ticker") or ""),
                    "target_weight": round(_safe_float(item.get("target_weight")), 6),
                }
                for item in list(row.get("targets") or [])
                if isinstance(item, dict)
                and len(str(item.get("ticker") or "")) == 6
                and str(item.get("ticker") or "").isdigit()
                and _safe_float(item.get("target_weight")) > 0
            ][:8]
            if not targets:
                continue
            context_rows.append(
                {
                    "as_of": str(row.get("as_of") or ""),
                    "status": str(row.get("status") or ""),
                    "targets": targets,
                }
            )

        churn_note = "리밸런싱 타깃 급변 주의: 직전 대비 완만 조정"
        if context_rows:
            prev_targets = {
                str(item.get("ticker") or ""): _safe_float(item.get("target_weight"))
                for item in list(context_rows[0].get("targets") or [])
                if isinstance(item, dict)
            }
            now_targets = {
                str(item.get("ticker") or ""): _safe_float(item.get("target_weight"))
                for item in current_targets
                if isinstance(item, dict)
            }
            all_keys = sorted(set(prev_targets.keys()) | set(now_targets.keys()))
            turnover = sum(
                abs(
                    _safe_float(now_targets.get(key))
                    - _safe_float(prev_targets.get(key))
                )
                for key in all_keys
            )
            changed = sum(
                1
                for key in all_keys
                if abs(
                    _safe_float(now_targets.get(key))
                    - _safe_float(prev_targets.get(key))
                )
                >= 0.03
            )
            churn_note = (
                "리밸런싱 타깃 급변 주의: "
                f"직전 대비 turnover {turnover*100:.1f}%p, 큰변화 {changed}종목"
            )

        history_hint = (
            "주의: 리밸런싱 타깃이 직전 대비 과도하게 급변하지 않도록 하세요. "
            "강한 신규 근거가 없으면 기존 타깃을 유지/완만 조정하고, 교체 종목 수를 최소화하세요. "
            f"현재 목표 현금비중은 {current_cash_weight*100:.1f}% 입니다."
        )
        return context_rows, f"{churn_note}. {history_hint}"

    def _build_rebalance_options(
        self,
        *,
        snapshot: dict[str, Any],
        enriched: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        strategy = self._build_strategy_spec()
        before_weights = {
            str(row.get("ticker") or ""): round(_safe_float(row.get("weight")), 4)
            for row in enriched
            if str(row.get("ticker") or "")
        }
        profiles = [
            ("A", 0.90, "보수형"),
            ("B", 1.00, "중립형"),
            ("C", 1.10, "기회형"),
        ]
        options: list[dict[str, Any]] = []
        base_cap = float(strategy["max_single_position_weight"])
        as_of = str(snapshot.get("as_of") or utc_now_iso())

        for label, multiplier, style in profiles[
            : max(int(self.config.option_count), 1)
        ]:
            cap = max(min(base_cap * multiplier, 0.8), 0.05)
            after = dict(before_weights)
            trades: list[dict[str, Any]] = []
            freed_weight = 0.0

            for ticker, weight in sorted(
                before_weights.items(), key=lambda item: item[1], reverse=True
            ):
                if weight <= cap:
                    continue
                delta = weight - cap
                after[ticker] = round(cap, 4)
                freed_weight += delta
                trades.append(
                    {
                        "ticker": ticker,
                        "action": "REDUCE",
                        "shares": int(round(delta * 100)),
                        "rationale_facts": [
                            f"비중 {weight*100:.1f}%가 정책 상한 {cap*100:.1f}%를 상회",
                            f"정책: max_single_position_weight={cap:.2f}",
                        ],
                    }
                )

            under = sorted(
                [(ticker, weight) for ticker, weight in after.items() if weight < cap],
                key=lambda item: item[1],
            )
            if freed_weight > 0 and under:
                total_gap = sum(cap - weight for _, weight in under)
                for ticker, weight in under:
                    if total_gap <= 0:
                        break
                    share = (cap - weight) / total_gap
                    add_weight = min(freed_weight * share, cap - weight)
                    if add_weight <= 0:
                        continue
                    after[ticker] = round(weight + add_weight, 4)
                    trades.append(
                        {
                            "ticker": ticker,
                            "action": "ADD",
                            "shares": int(round(add_weight * 100)),
                            "rationale_facts": [
                                f"정책 상한 내에서 분산 비중 보강({weight*100:.1f}% -> {after[ticker]*100:.1f}%)",
                                f"리밸런싱 주기: {strategy['rebalance_frequency']}",
                            ],
                        }
                    )

            options.append(
                {
                    "option_id": label,
                    "style": style,
                    "before_weights": before_weights,
                    "after_weights": after,
                    "trades": trades,
                    "target_single_weight": cap,
                    "trade_summary": (
                        f"상한 {cap*100:.0f}% 기준 조정 후보 {sum(max(t.get('shares', 0), 0) for t in trades)}주"
                        if trades
                        else "조정 필요 항목 없음"
                    ),
                    "constraints_ok": all(
                        weight <= cap + 1e-6 for weight in after.values()
                    ),
                    "cost_note": "수수료/세금 추정 미반영",
                    "citations": [
                        f"POLICY(max_single_position_weight={cap:.2f})",
                        f"DATA({as_of})",
                    ],
                }
            )
        return options[: max(int(self.config.option_count), 1)]

    def _build_playbook(
        self,
        *,
        snapshot: dict[str, Any],
        enriched: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        as_of = str(snapshot.get("as_of") or utc_now_iso())
        rows = sorted(
            enriched, key=lambda row: _safe_float(row.get("weight")), reverse=True
        )
        triggers: list[dict[str, Any]] = []

        if rows:
            top = rows[0]
            name = str(top.get("name") or top.get("ticker") or "주요 보유")
            day_move = _safe_float(top.get("day_change_pct"))
            triggers.append(
                {
                    "trigger": f"비중 상위 종목({name}) 일중 -3% 이하 변동",
                    "observed_facts": [
                        f"현재 당일 변동률 {day_move:+.2f}%",
                        f"비중 {_safe_float(top.get('weight'))*100:.1f}%",
                    ],
                    "options": [
                        "원인 뉴스/공시를 먼저 확인",
                        "정책 범위 내 분산 옵션 비교",
                    ],
                    "checks": ["장중 수급 변화 확인", "리포트 가정 훼손 여부 확인"],
                    "citations": [f"DATA({as_of})"],
                }
            )

        for row in rows[:2]:
            facts_rows = list(row.get("report_facts") or [])
            if not facts_rows:
                continue
            latest = facts_rows[0]
            broker = str(latest.get("broker") or "-")
            published = str(latest.get("published_at") or "-")
            risk = str((latest.get("risks") or ["근거 부족"])[0])
            name = str(row.get("name") or row.get("ticker") or "종목")
            triggers.append(
                {
                    "trigger": f"{name} 리포트/공시 이벤트 발생",
                    "observed_facts": [risk[:160], f"최신 리포트 일자 {published}"],
                    "options": [
                        "기존 옵션 A/B/C 중 제약 충족안 재검토",
                        "추가 리포트 1건 이상 확인 후 판단",
                    ],
                    "checks": ["리포트 원문 문장 확인", "기존 가정 대비 변경폭 비교"],
                    "citations": [f"[{broker}, {published}, p.?]"],
                }
            )
        if len(triggers) < 2:
            triggers.append(
                {
                    "trigger": "리포트/시세 데이터 업데이트 감지",
                    "observed_facts": ["신규 근거 반영 필요"],
                    "options": [
                        "신규 데이터 반영 후 옵션 재계산",
                        "정책 제약 충족 여부 재검토",
                    ],
                    "checks": ["인덱싱 상태 확인", "티커 매핑 확인"],
                    "citations": ["DATA(update_event)"] if as_of else ["근거 부족"],
                }
            )
        return triggers[: max(int(self.config.trigger_count), 1)]

    def _build_market_mood(
        self,
        *,
        as_of: str,
        enriched: list[dict[str, Any]],
        reports_covered: int,
        tickers_covered: int,
    ) -> list[str]:
        date_kst, time_kst = _to_kst_date_time(as_of)
        day_change_rows = [
            _safe_float(row.get("day_change_pct"))
            for row in enriched
            if _safe_float(row.get("last_price")) > 0
        ]
        weighted_move = 0.0
        for row in enriched:
            weighted_move += _safe_float(row.get("weight")) * _safe_float(
                row.get("day_change_pct")
            )
        concentration = max(
            (_safe_float(row.get("weight")) for row in enriched), default=0.0
        )

        mood_label = "중립"
        if weighted_move >= 0.8:
            mood_label = "리스크온"
        elif weighted_move <= -0.8:
            mood_label = "리스크오프"

        quality = "정상"
        if reports_covered <= 0 or tickers_covered <= 0:
            quality = "제한"
        elif len(day_change_rows) <= 0:
            quality = "부분제한"

        lines = [
            f"데이터 기준시각(as_of): {date_kst} {time_kst} KST",
            f"시장 온도: {mood_label} (보유 가중 변동 {weighted_move:+.2f}%)",
            f"집중도 신호: 최대 비중 {concentration*100:.1f}%",
            f"리포트 커버리지: {reports_covered}건 / {tickers_covered}종목",
            f"데이터 상태: {quality} (휴장/시세제한 시 보수적 해석)",
        ]
        weekday = (
            (_parse_date_value(as_of) or datetime.now(KST)).astimezone(KST).weekday()
        )
        if weekday >= 5:
            lines.append(
                "휴장 구간 가능성: 실시간 체결 기반 수치 대신 직전 기준 데이터 사용"
            )
        return lines[:6]

    def _build_portfolio_status(
        self,
        *,
        snapshot: dict[str, Any],
        enriched: list[dict[str, Any]],
    ) -> dict[str, Any]:
        total = sum(
            _safe_float(row.get("market_value")) for row in enriched
        ) + _safe_float(snapshot.get("cash"))
        cash = _safe_float(snapshot.get("cash"))
        top_rows = sorted(
            enriched,
            key=lambda row: _safe_float(row.get("weight")),
            reverse=True,
        )[:3]
        holdings_summary = ", ".join(
            [
                _name_code(str(row.get("name") or ""), str(row.get("ticker") or ""))
                for row in top_rows
            ]
        )
        max_weight = max(
            (_safe_float(row.get("weight")) for row in enriched), default=0.0
        )
        concentration = f"최대 비중 {max_weight*100:.1f}% / 정책 상한 {float(self.config.max_single_position_weight)*100:.1f}%"
        return {
            "total": f"{int(round(total)):,}원",
            "cash": f"{int(round(cash)):,}원",
            "holdings_summary": holdings_summary or "보유 상위 종목 데이터 없음",
            "concentration": concentration,
        }

    def _build_data_gaps(
        self,
        *,
        ideas: list[dict[str, Any]],
        reports_covered: int,
        tickers_covered: int,
        name_mapping_failures: int,
    ) -> tuple[list[str], list[str]]:
        gaps: list[str] = []
        next_actions: list[str] = []
        if reports_covered <= 0:
            gaps.append("리포트 커버리지 0건")
            next_actions.append("리포트 수집/인덱싱 상태 점검")
        if tickers_covered <= 0:
            gaps.append("티커 커버리지 0개")
            next_actions.append("리포트 symbol/티커 매핑 규칙 점검")
        if len(ideas) < 5:
            gaps.append(f"신규 아이디어 부족({len(ideas)}개)")
            next_actions.append(
                "lookback 확장(90->180), rating 범위 확장(BUY->BUY+HOLD) 재확인"
            )
        if name_mapping_failures > 0:
            gaps.append(f"name mapping 실패 {name_mapping_failures}건")
            next_actions.append("ticker->name 매핑 규칙/외부 소스 상태 점검")
        return gaps, next_actions

    def _resolve_security_name(
        self,
        ticker: str,
        *candidates: str,
    ) -> str:
        key = str(ticker or "").strip()
        cached = str(self._name_cache.get(key) or "").strip()
        if cached and not cached.startswith("UNKNOWN_NAME"):
            return cached

        alias_name = _clean_line(self._ticker_name_map.get(key), 40)
        if alias_name and not (len(alias_name) == 6 and alias_name.isdigit()):
            self._name_cache[key] = alias_name
            return alias_name

        if (
            len(key) == 6
            and key.isdigit()
            and hasattr(self.report_repo, "get_symbol_name")
        ):
            try:
                db_name = _clean_line(self.report_repo.get_symbol_name(key), 40)
            except Exception:
                db_name = ""
            if db_name and not (len(db_name) == 6 and db_name.isdigit()):
                self._name_cache[key] = db_name
                return db_name

        for item in candidates:
            name = _clean_line(item, 40)
            if name and not (len(name) == 6 and name.isdigit()):
                self._name_cache[key] = name
                return name

        if len(key) == 6 and key.isdigit() and hasattr(self.report_repo, "search"):
            try:
                rows = self.report_repo.search(
                    query="", symbol=key, category="", limit=3
                )
            except Exception:
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                report_name = _clean_line(row.get("company_name"), 40)
                if report_name and not (
                    len(report_name) == 6 and report_name.isdigit()
                ):
                    if hasattr(self.report_repo, "upsert_symbol_directory"):
                        try:
                            self.report_repo.upsert_symbol_directory(
                                symbol=key,
                                company_name=report_name,
                                source="portfolio_coach_report_repo",
                                confidence=0.9,
                                status="active",
                            )
                        except Exception:
                            pass
                    self._name_cache[key] = report_name
                    return report_name

        if len(key) == 6 and key.isdigit():
            try:
                from pykrx import stock  # type: ignore

                resolved = _clean_line(stock.get_market_ticker_name(key), 40)
                if resolved and not (len(resolved) == 6 and resolved.isdigit()):
                    if hasattr(self.report_repo, "upsert_symbol_directory"):
                        try:
                            self.report_repo.upsert_symbol_directory(
                                symbol=key,
                                company_name=resolved,
                                source="portfolio_coach_pykrx",
                                confidence=1.0,
                                status="active",
                            )
                        except Exception:
                            pass
                    self._name_cache[key] = resolved
                    return resolved
            except Exception:
                pass

        fallback = key or "UNKNOWN_NAME"
        self._name_cache[key] = fallback
        return fallback

    def _theme_key(self, *, category: str, name: str, title: str) -> str:
        haystack = f"{category} {name} {title}".lower()
        mapping: list[tuple[str, str]] = [
            ("반도체", "semiconductor"),
            ("메모리", "semiconductor"),
            ("자동차", "auto"),
            ("차", "auto"),
            ("은행", "financial"),
            ("보험", "financial"),
            ("2차전지", "battery"),
            ("배터리", "battery"),
            ("바이오", "bio"),
            ("제약", "bio"),
            ("인터넷", "internet"),
            ("게임", "internet"),
            ("화학", "chem"),
            ("철강", "materials"),
            ("조선", "ship"),
        ]
        for keyword, label in mapping:
            if keyword in haystack:
                return label
        return _clean_line(category, 24) or "general"

    def _report_citations_from_row(self, row: dict[str, Any]) -> list[str]:
        broker = _clean_line(row.get("broker"), 24) or "-"
        published = _clean_line(row.get("published_at"), 10) or "-"
        out: list[str] = []
        for quote in list(row.get("evidence_quotes") or []):
            if not isinstance(quote, dict):
                continue
            page = _safe_int(quote.get("page"))
            if page <= 0:
                continue
            out.append(f"[{broker}, {published}, p.{page}]")
        return list(dict.fromkeys(out))

    def _collect_recent_report_rows(self, lookback_days: int) -> list[dict[str, Any]]:
        if hasattr(self.report_repo, "list_recent_report_facts"):
            try:
                rows = self.report_repo.list_recent_report_facts(
                    lookback_days=lookback_days,
                    limit=max(int(self.config.max_candidates) * 250, 1500),
                )
                return [row for row in rows if isinstance(row, dict)]
            except Exception:
                pass

        fallback: list[dict[str, Any]] = []
        rows = self.report_repo.search(query="", symbol="", category="", limit=100)
        for row in rows:
            if not isinstance(row, dict):
                continue
            report_id = _safe_int(row.get("report_id"))
            if report_id <= 0:
                continue
            facts = self.report_repo.get_report_facts(report_id)
            if not isinstance(facts, dict):
                continue
            fallback.append(
                {
                    "report_id": report_id,
                    "symbol": str(row.get("symbol") or ""),
                    "company_name": str(row.get("company_name") or ""),
                    "title": str(row.get("title") or ""),
                    "broker": str(row.get("broker") or ""),
                    "category": str(row.get("category") or "unknown"),
                    "published_at": str(row.get("published_at") or ""),
                    "rating": str(facts.get("rating") or "UNKNOWN"),
                    "target_price_value": _safe_int(
                        (facts.get("target_price") or {}).get("value")
                    ),
                    "catalysts": list(facts.get("catalysts") or []),
                    "risks": list(facts.get("risks") or []),
                    "investment_thesis": list(facts.get("investment_thesis") or []),
                    "evidence_quotes": list(facts.get("evidence_quotes") or []),
                }
            )
        return fallback

    async def _fetch_quote_for_ticker(
        self,
        ticker: str,
        *,
        as_of: str,
    ) -> dict[str, Any]:
        if self.kis is None:
            return {
                "ticker": ticker,
                "name": ticker,
                "last_price": 0.0,
                "day_change_pct": 0.0,
                "as_of": as_of,
            }
        try:
            quote = await self.kis.fetch_domestic_quote(ticker)
            raw_obj = quote.get("raw")
            raw: dict[str, Any] = raw_obj if isinstance(raw_obj, dict) else {}
            return {
                "ticker": ticker,
                "name": str(quote.get("name") or ticker),
                "last_price": _safe_float(quote.get("price")),
                "day_change_pct": _safe_float(
                    raw.get("stck_prdy_ctrt") or raw.get("prdy_ctrt") or 0.0
                ),
                "as_of": as_of,
            }
        except Exception:
            return {
                "ticker": ticker,
                "name": ticker,
                "last_price": 0.0,
                "day_change_pct": 0.0,
                "as_of": as_of,
            }

    def _action_entry(self, as_of: str) -> str:
        date_kst, time_kst = _to_kst_date_time(as_of)
        return f"MARKET(as_of={date_kst} {time_kst} KST)"

    def _build_hold_action(
        self,
        *,
        action: str,
        as_of: str,
        reason: str,
        next_action: str,
        ticker: str = "",
        name: str = "보류",
    ) -> dict[str, Any]:
        return {
            "action": action,
            "status": "ON_HOLD",
            "ticker": ticker,
            "name": name,
            "size": {"kind": "SHARES", "value": 0},
            "entry": self._action_entry(as_of),
            "rationale_bullets": [reason],
            "key_numbers": [f"다음 액션: {next_action}"],
            "risks": [reason],
            "invalidation": ["근거 보강 시 재평가"],
            "evidence": ["근거 부족"],
        }

    async def _supplement_citations_with_rag(
        self,
        *,
        ticker: str,
        name: str,
        as_of: str,
        citations: list[str],
    ) -> list[str]:
        out = list(
            dict.fromkeys(
                [_clean_line(item, 70) for item in citations if _clean_line(item, 70)]
            )
        )
        if len(out) >= 2:
            return out[:2]
        if self.rag_store is None or not bool(
            getattr(self.rag_store, "available", False)
        ):
            return out[:2]

        as_of_date = _iso_date(as_of)
        as_of_dt = _parse_date_value(as_of) or datetime.now(KST)
        date_from = (
            (
                as_of_dt
                - timedelta(days=max(int(self.config.direct_report_lookback_days), 1))
            )
            .date()
            .isoformat()
        )
        queries = [
            "투자의견 목표주가 핵심 근거",
            "핵심 리스크",
            "실적 전망 가정",
        ]
        for query in queries:
            try:
                rows = self.rag_store.query(
                    query=f"{name} {ticker} {query}",
                    symbol=ticker,
                    date_from=date_from,
                    date_to=as_of_date,
                    limit=6,
                )
            except Exception:
                rows = []
            for row in rows:
                broker = _clean_line(row.get("broker"), 24) or "-"
                published = _clean_line(row.get("published_at"), 10) or "-"
                page = _safe_int(row.get("page_start") or row.get("page_end"))
                if page <= 0:
                    continue
                cite = f"[{broker}, {published}, p.{page}]"
                if cite not in out:
                    out.append(cite)
                if len(out) >= 2:
                    return out[:2]
        return out[:2]

    async def _build_actionable_pack(
        self,
        *,
        snapshot: dict[str, Any],
        enriched: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        _ = candidates
        as_of = str(snapshot.get("as_of") or utc_now_iso())
        date_kst, time_kst = _to_kst_date_time(as_of)
        data_ref = f"DATA(as_of={date_kst} {time_kst} KST)"
        entry = self._action_entry(as_of)
        max_cap = float(self.config.max_single_position_weight)

        report_rows = self._collect_recent_report_rows(lookback_days=90)
        if len(report_rows) < 300:
            report_rows = self._collect_recent_report_rows(lookback_days=180)
        reports_covered = len(report_rows)
        aggregates: dict[str, dict[str, Any]] = {}
        for row in report_rows:
            ticker = str(row.get("symbol") or "").strip()
            if len(ticker) != 6 or not ticker.isdigit():
                continue
            rating_raw = str(row.get("rating") or "").upper().strip()
            tp_raw = _safe_float(row.get("target_price_value"))
            if not rating_raw or rating_raw == "UNKNOWN" or tp_raw <= 0:
                continue
            bucket = aggregates.get(ticker)
            if bucket is None:
                bucket = {
                    "ticker": ticker,
                    "name": "",
                    "category": str(row.get("category") or "unknown"),
                    "title": str(row.get("title") or ""),
                    "rows": [],
                    "ratings": [],
                    "targets": [],
                    "citations": [],
                    "risks": [],
                    "catalysts": [],
                    "thesis": [],
                    "last_update": "",
                }
                aggregates[ticker] = bucket

            name_hint = self._resolve_security_name(
                ticker,
                str(row.get("company_name") or ""),
                str(row.get("title") or ""),
            )
            if not bucket["name"] and name_hint:
                bucket["name"] = name_hint

            bucket["rows"].append(row)
            rating = rating_raw
            if rating:
                bucket["ratings"].append(rating)
            target = tp_raw
            if target > 0:
                bucket["targets"].append(target)
            bucket["citations"].extend(self._report_citations_from_row(row))
            bucket["risks"].extend(
                [
                    _clean_line(item, 90)
                    for item in list(row.get("risks") or [])
                    if _clean_line(item, 90)
                ]
            )
            bucket["catalysts"].extend(
                [
                    _clean_line(item, 90)
                    for item in list(row.get("catalysts") or [])
                    if _clean_line(item, 90)
                ]
            )
            bucket["thesis"].extend(
                [
                    _clean_line(item, 90)
                    for item in list(row.get("investment_thesis") or [])
                    if _clean_line(item, 90)
                ]
            )
            published = str(row.get("published_at") or "")
            if published and (
                not bucket["last_update"] or published > bucket["last_update"]
            ):
                bucket["last_update"] = published

        tickers_covered = len(aggregates)
        reject_counter: Counter[str] = Counter()
        name_mapping_failures = 0
        enriched_by_ticker = {
            str(row.get("ticker") or ""): row
            for row in enriched
            if str(row.get("ticker") or "")
        }
        held_tickers = {
            str(row.get("ticker") or "")
            for row in enriched
            if str(row.get("ticker") or "")
        }

        buy_ratings = {"BUY", "OUTPERFORM", "STRONG_BUY", "OVERWEIGHT"}
        hold_like_ratings = {"HOLD", "NEUTRAL", "MARKETPERFORM"}
        as_of_dt = _parse_date_value(as_of) or datetime.now(KST)

        async def build_idea(ticker: str) -> tuple[dict[str, Any] | None, str]:
            agg = aggregates.get(ticker)
            if not isinstance(agg, dict):
                return None, "aggregation_missing"

            rating_counter = Counter(
                str(item).upper()
                for item in list(agg.get("ratings") or [])
                if str(item)
            )
            if not rating_counter:
                return None, "rating_missing"
            rating_consensus = rating_counter.most_common(1)[0][0]

            targets = [
                _safe_float(item)
                for item in list(agg.get("targets") or [])
                if _safe_float(item) > 0
            ]
            if not targets:
                return None, "target_price_missing"
            sorted_targets = sorted(targets)
            mid = len(sorted_targets) // 2
            if len(sorted_targets) % 2 == 1:
                tp_consensus = sorted_targets[mid]
            else:
                tp_consensus = (sorted_targets[mid - 1] + sorted_targets[mid]) / 2.0
            if tp_consensus <= 0:
                return None, "target_price_missing"

            quote = await self._fetch_quote_for_ticker(ticker, as_of=as_of)
            last_price = _safe_float(quote.get("last_price"))
            if last_price <= 0:
                held = enriched_by_ticker.get(ticker)
                if isinstance(held, dict):
                    qty = _safe_float(held.get("quantity"))
                    market_value = _safe_float(held.get("market_value"))
                    if qty > 0 and market_value > 0:
                        last_price = market_value / qty
            if last_price <= 0:
                return None, "last_price_missing"

            name = self._resolve_security_name(
                ticker,
                str(agg.get("name") or ""),
                str(quote.get("name") or ""),
            )
            if not name or name == ticker:
                nonlocal name_mapping_failures
                name_mapping_failures += 1

            citations = [
                _clean_line(item, 70)
                for item in list(agg.get("citations") or [])
                if _clean_line(item, 70)
            ]
            citations = await self._supplement_citations_with_rag(
                ticker=ticker,
                name=name,
                as_of=as_of,
                citations=citations,
            )
            if len(citations) < 2:
                if not citations:
                    citations.append("[근거보강, -, p.?]")
                citations.append(f"DATA(as_of={date_kst} {time_kst} KST)")
                citations = list(dict.fromkeys(citations))

            latest = str(agg.get("last_update") or "")
            latest_dt = _parse_date_value(latest) or as_of_dt
            recency_days = max((as_of_dt.date() - latest_dt.date()).days, 0)
            recency_score = max(0.0, 1.0 - (recency_days / 180.0))

            variance = 0.0
            if len(targets) >= 2:
                mean_tp = tp_consensus
                variance = sum((x - mean_tp) ** 2 for x in targets) / float(
                    len(targets)
                )
            dispersion = math.sqrt(variance) / max(tp_consensus, 1.0)

            risk_text = " ".join(list(agg.get("risks") or []))
            risk_keywords = ["하방", "리스크", "risk", "둔화", "규제", "변동"]
            risk_penalty = sum(
                1 for keyword in risk_keywords if keyword.lower() in risk_text.lower()
            )
            risk_penalty = min(risk_penalty, 4)

            upside = (tp_consensus / last_price) - 1.0
            coverage_count = len(list(agg.get("rows") or []))
            score = (
                (100.0 * upside)
                + (10.0 * recency_score)
                + (5.0 * math.log(coverage_count + 1.0))
                - (20.0 * dispersion)
                - (8.0 * float(risk_penalty))
            )

            return (
                {
                    "ticker": ticker,
                    "name": name,
                    "rating_consensus": rating_consensus,
                    "tp_consensus": int(round(tp_consensus)),
                    "upside_pct": round(upside * 100.0, 1),
                    "coverage_count": coverage_count,
                    "last_update": latest,
                    "last_price": int(round(last_price)),
                    "bull_points": list(
                        dict.fromkeys(list(agg.get("catalysts") or [])[:3])
                    )
                    or list(dict.fromkeys(list(agg.get("thesis") or [])[:2]))
                    or ["핵심 투자포인트 확인 필요"],
                    "bear_points": list(dict.fromkeys(list(agg.get("risks") or [])[:3]))
                    or ["리스크 문구 부족"],
                    "what_to_watch": [
                        "다음 리포트/공시 업데이트",
                        "실적 가정 변동 여부",
                    ],
                    "why": (
                        f"{rating_consensus} 컨센서스와 TP {int(round(tp_consensus)):,}원 기준 상승여력 "
                        f"{upside*100:.1f}%로 모델 포트 편입 우선순위가 높음"
                    ),
                    "evidence": citations[:2],
                    "theme": self._theme_key(
                        category=str(agg.get("category") or "unknown"),
                        name=name,
                        title=str(agg.get("title") or ""),
                    ),
                    "base_score": score,
                },
                "",
            )

        selected_ideas: list[dict[str, Any]] = []
        selected_tickers: set[str] = set()
        theme_counter: Counter[str] = Counter()
        passes = [
            (120, buy_ratings | hold_like_ratings),
            (240, buy_ratings | hold_like_ratings),
            (365, None),
        ]

        for max_days, allowed_ratings in passes:
            if len(selected_ideas) >= 6:
                break
            pool: list[dict[str, Any]] = []
            for ticker, agg in aggregates.items():
                if ticker in selected_tickers:
                    continue
                rating_counter = Counter(
                    str(item).upper()
                    for item in list(agg.get("ratings") or [])
                    if str(item)
                )
                if not rating_counter:
                    reject_counter["rating_missing"] += 1
                    continue
                rating = rating_counter.most_common(1)[0][0]
                if allowed_ratings is not None and rating not in allowed_ratings:
                    reject_counter["rating_not_eligible"] += 1
                    continue
                latest = _parse_date_value(str(agg.get("last_update") or ""))
                days = max((as_of_dt.date() - (latest or as_of_dt).date()).days, 0)
                if days > max_days:
                    reject_counter["report_stale"] += 1
                    continue
                idea, reason = await build_idea(ticker)
                if idea is None:
                    reject_counter[reason or "idea_build_failed"] += 1
                    continue
                upside = _safe_float(idea.get("upside_pct")) / 100.0
                if upside < float(self.config.buy_min_upside) and rating in buy_ratings:
                    reject_counter["upside_below_min"] += 1
                    continue
                pool.append(idea)

            if not pool and len(selected_ideas) < 5 and allowed_ratings is None:
                for ticker in aggregates.keys():
                    if ticker in selected_tickers:
                        continue
                    idea, reason = await build_idea(ticker)
                    if idea is None:
                        reject_counter[reason or "idea_build_failed"] += 1
                        continue
                    pool.append(idea)

            while pool and len(selected_ideas) < 6:
                best_idx = 0
                best_score = -(10**9)
                for idx, idea in enumerate(pool):
                    theme = str(idea.get("theme") or "general")
                    adjusted = _safe_float(idea.get("base_score")) - (
                        3.0 * float(theme_counter.get(theme, 0))
                    )
                    if adjusted > best_score:
                        best_score = adjusted
                        best_idx = idx
                chosen = pool.pop(best_idx)
                ticker = str(chosen.get("ticker") or "")
                if ticker in selected_tickers:
                    continue
                selected_tickers.add(ticker)
                theme_counter[str(chosen.get("theme") or "general")] += 1
                selected_ideas.append(chosen)

        selected_ideas.sort(
            key=lambda row: _safe_float(row.get("base_score")), reverse=True
        )
        selected_ideas = selected_ideas[:6]

        if not selected_ideas:
            fallback_tickers: list[str] = []
            for ticker, agg in sorted(
                aggregates.items(),
                key=lambda item: len(list(item[1].get("rows") or [])),
                reverse=True,
            ):
                text = str(ticker or "").strip()
                if len(text) != 6 or not text.isdigit():
                    continue
                if text in held_tickers or text in fallback_tickers:
                    continue
                fallback_tickers.append(text)
                if len(fallback_tickers) >= 6:
                    break

            for ticker in fallback_tickers:
                quote = await self._fetch_quote_for_ticker(ticker, as_of=as_of)
                last_price = _safe_float(quote.get("last_price"))
                if last_price <= 0:
                    continue
                selected_ideas.append(
                    {
                        "ticker": ticker,
                        "name": self._resolve_security_name(
                            ticker,
                            str(quote.get("name") or ""),
                        ),
                        "rating_consensus": "HOLD",
                        "tp_consensus": int(round(last_price * 1.03)),
                        "upside_pct": 3.0,
                        "coverage_count": len(
                            list((aggregates.get(ticker) or {}).get("rows") or [])
                        ),
                        "last_update": date_kst,
                        "last_price": int(round(last_price)),
                        "bull_points": ["리포트/시세 결합 기반 신규 후보"],
                        "bear_points": ["목표주가/리포트 근거 추가 점검 필요"],
                        "what_to_watch": ["장중 변동률", "시세 안정성"],
                        "why": "리포트 다건+실시간 시세 확인된 기본 후보",
                        "evidence": [
                            f"DATA(as_of={date_kst} {time_kst} KST)",
                            "[근거보강, -, p.?]",
                        ],
                        "theme": "fallback",
                        "base_score": float(
                            len(list((aggregates.get(ticker) or {}).get("rows") or []))
                        ),
                    }
                )

            for row in sorted(
                enriched,
                key=lambda item: _safe_float(item.get("weight")),
                reverse=True,
            )[:6]:
                if len(selected_ideas) >= 6:
                    break
                ticker = str(row.get("ticker") or "")
                if not ticker:
                    continue
                if any(
                    str(item.get("ticker") or "") == ticker for item in selected_ideas
                ):
                    continue
                name = self._resolve_security_name(ticker, str(row.get("name") or ""))
                last_price = _safe_float(row.get("last_price"))
                if last_price <= 0:
                    qty = _safe_float(row.get("quantity"))
                    market_value = _safe_float(row.get("market_value"))
                    if qty > 0 and market_value > 0:
                        last_price = market_value / qty
                if last_price <= 0:
                    continue
                selected_ideas.append(
                    {
                        "ticker": ticker,
                        "name": name,
                        "rating_consensus": "HOLD",
                        "tp_consensus": int(round(last_price * 1.05)),
                        "upside_pct": 5.0,
                        "coverage_count": 1,
                        "last_update": date_kst,
                        "last_price": int(round(last_price)),
                        "bull_points": ["보유자산 기준 기본 리밸런싱 후보"],
                        "bear_points": ["리포트 근거 보강 필요"],
                        "what_to_watch": ["장중 변동률", "리포트 업데이트"],
                        "why": "보유 비중/가격 데이터 기반 기본 추천 후보",
                        "evidence": [
                            f"DATA(as_of={date_kst} {time_kst} KST)",
                            "[근거보강, -, p.?]",
                        ],
                        "theme": "fallback",
                        "base_score": _safe_float(row.get("weight")) * 10.0,
                    }
                )
            selected_ideas = selected_ideas[:6]

        decided_target_cash_weight = self._decide_target_cash_weight(
            selected_ideas=selected_ideas,
            reports_covered=reports_covered,
            tickers_covered=tickers_covered,
        )

        planner = RebalancePlanner(
            RebalancePlannerConfig(
                target_cash_weight=decided_target_cash_weight,
                max_single_weight=float(self.config.max_single_position_weight),
                target_positions=int(self.config.target_positions),
                max_trades_per_message=int(self.config.max_trades_per_message),
                min_trade_krw=float(self.config.min_trade_krw),
            )
        )
        rebalance = planner.plan(
            snapshot=snapshot,
            enriched=enriched,
            ideas=selected_ideas,
            resolve_name=lambda ticker, *cands: self._resolve_security_name(
                ticker, *cands
            ),
            data_ref=data_ref,
            as_of=f"{date_kst} {time_kst} KST",
        )

        model_targets: list[dict[str, Any]] = []
        target_map = dict(rebalance.get("target_allocation") or {})
        for row in selected_ideas[:6]:
            ticker = str(row.get("ticker") or "")
            target_weight = _safe_float(target_map.get(ticker))
            if target_weight <= 0:
                continue
            name = self.security_resolver.resolve(ticker, str(row.get("name") or ""))
            model_targets.append(
                {
                    "name": name,
                    "ticker": ticker,
                    "target_weight": round(target_weight, 4),
                    "rating_consensus": str(row.get("rating_consensus") or "UNKNOWN"),
                    "tp_consensus": _safe_int(row.get("tp_consensus")),
                    "upside_pct": round(_safe_float(row.get("upside_pct")), 1),
                    "why": _clean_line(row.get("why"), 120)
                    or "리서치 컨센서스와 업사이드 기반 장투 모델 편입",
                    "bull_points": [
                        _clean_line(item, 90)
                        for item in list(row.get("bull_points") or [])
                        if _clean_line(item, 90)
                    ][:2],
                    "bear_points": [
                        _clean_line(item, 90)
                        for item in list(row.get("bear_points") or [])
                        if _clean_line(item, 90)
                    ][:2],
                    "evidence": [
                        _clean_line(item, 70)
                        for item in list(row.get("evidence") or [])
                        if _clean_line(item, 70)
                    ][:2],
                }
            )

        plan_trades_raw = [
            row for row in list(rebalance.get("trades") or []) if isinstance(row, dict)
        ]
        action_trades: list[dict[str, Any]] = []
        for row in plan_trades_raw:
            ticker = str(row.get("ticker") or "")
            action = str(row.get("action") or "HOLD").upper()
            name = self.security_resolver.resolve(ticker, str(row.get("name") or ""))
            est_shares = max(_safe_int(row.get("est_shares")), 0)
            target_w = _safe_float(row.get("target_weight"))
            current_w = _safe_float(row.get("current_weight"))
            action_trades.append(
                {
                    "action": action,
                    "status": "PROPOSED",
                    "ticker": ticker,
                    "name": name,
                    "size": {"kind": "SHARES", "value": est_shares},
                    "entry": entry,
                    "rationale_bullets": [
                        _clean_line(row.get("reason"), 100)
                        or "목표 비중 대비 조정 필요",
                        f"목표 {target_w*100:.1f}% / 현재 {current_w*100:.1f}%",
                    ],
                    "key_numbers": [
                        f"est_krw {_safe_int(row.get('est_krw')):,}원",
                        f"delta_weight {(target_w-current_w)*100:+.1f}%p",
                    ],
                    "risks": ["장중 변동성으로 체결가 오차 가능"],
                    "invalidation": ["리포트 의견/목표주가 변경 시 재산출"],
                    "evidence": [
                        _clean_line(item, 70)
                        for item in list(row.get("evidence") or [])
                        if _clean_line(item, 70)
                    ][:2],
                }
            )

        rebalance_rows = [
            row
            for row in list(rebalance.get("rebalance_table_rows") or [])
            if isinstance(row, dict)
        ]
        if rebalance_rows:
            top_row = sorted(
                rebalance_rows,
                key=lambda item: abs(_safe_float(item.get("delta_weight"))),
                reverse=True,
            )[0]
            action_trades.insert(
                0,
                {
                    "action": "REBALANCE",
                    "status": "PROPOSED",
                    "ticker": str(top_row.get("ticker") or ""),
                    "name": self.security_resolver.resolve(
                        str(top_row.get("ticker") or ""), str(top_row.get("name") or "")
                    ),
                    "size": {
                        "kind": "TARGET_WEIGHT",
                        "value": round(_safe_float(top_row.get("target_weight")), 4),
                    },
                    "entry": entry,
                    "rationale_bullets": [
                        "모델 포트 목표 비중 기준 리밸런싱 실행안",
                        f"변동 절대폭 {abs(_safe_float(top_row.get('delta_weight')))*100:.1f}%p 우선 조정",
                    ],
                    "key_numbers": [
                        f"target_cash_weight {decided_target_cash_weight*100:.1f}%",
                        f"max_single_weight {float(self.config.max_single_position_weight)*100:.1f}%",
                    ],
                    "risks": ["수수료/세금 추정 미반영"],
                    "invalidation": ["데이터 기준시각 갱신 시 재계산"],
                    "evidence": [data_ref, data_ref],
                },
            )

        top_holding = max(
            enriched, key=lambda row: _safe_float(row.get("weight")), default={}
        )
        top_weight = _safe_float(top_holding.get("weight"))
        if top_weight > float(self.config.max_single_position_weight):
            has_reduce_like = any(
                str(row.get("action") or "").upper() in {"REDUCE", "SELL"}
                for row in action_trades
            )
            if not has_reduce_like:
                top_ticker = str(top_holding.get("ticker") or "")
                top_name = self.security_resolver.resolve(
                    top_ticker, str(top_holding.get("name") or "")
                )
                action_trades.insert(
                    0,
                    {
                        "action": "REDUCE",
                        "status": "PROPOSED",
                        "ticker": top_ticker,
                        "name": top_name,
                        "size": {
                            "kind": "TARGET_WEIGHT",
                            "value": round(
                                float(self.config.max_single_position_weight), 4
                            ),
                        },
                        "entry": entry,
                        "rationale_bullets": [
                            f"집중도 교정: 현재 {top_weight*100:.1f}% > 상한 {float(self.config.max_single_position_weight)*100:.1f}%",
                            "모델 포트 분산 목표 유지를 위한 우선 축소",
                        ],
                        "key_numbers": [
                            f"delta_weight {(top_weight-float(self.config.max_single_position_weight))*100:.1f}%p",
                            f"target_weight {float(self.config.max_single_position_weight)*100:.1f}%",
                        ],
                        "risks": ["리밸런싱 시점 체결가 변동 가능"],
                        "invalidation": ["집중도 하락 또는 정책 변경 시 재검토"],
                        "evidence": [data_ref, data_ref],
                    },
                )

        while len(action_trades) < 3:
            action_trades.append(
                self._build_hold_action(
                    action=["REDUCE", "BUY", "REBALANCE"][len(action_trades) % 3],
                    as_of=as_of,
                    reason="제안 보류(근거 부족: 실행안 최소 조건 미충족)",
                    next_action="리포트/시세/매핑 데이터 보강 후 재생성",
                )
            )
        action_trades = action_trades[:3]

        positions_rows = sorted(
            enriched,
            key=lambda row: _safe_float(row.get("weight")),
            reverse=True,
        )[:6]
        portfolio_positions = [
            {
                "name": self.security_resolver.resolve(
                    str(row.get("ticker") or ""), str(row.get("name") or "")
                ),
                "ticker": str(row.get("ticker") or ""),
                "weight": round(_safe_float(row.get("weight")), 4),
                "market_value": _safe_int(row.get("market_value")),
            }
            for row in positions_rows
            if str(row.get("ticker") or "")
        ]

        gaps, next_actions = self._build_data_gaps(
            ideas=selected_ideas,
            reports_covered=reports_covered,
            tickers_covered=tickers_covered,
            name_mapping_failures=name_mapping_failures,
        )
        filter_rejects = [
            {"reason": reason, "count": count}
            for reason, count in reject_counter.most_common(3)
        ]

        market_mood = self._build_market_mood(
            as_of=as_of,
            enriched=enriched,
            reports_covered=reports_covered,
            tickers_covered=tickers_covered,
        )
        portfolio_status = self._build_portfolio_status(
            snapshot=snapshot, enriched=enriched
        )
        current_targets_for_history = [
            {
                "ticker": str(row.get("ticker") or ""),
                "name": str(row.get("name") or ""),
                "target_weight": round(_safe_float(row.get("target_weight")), 6),
            }
            for row in model_targets
            if len(str(row.get("ticker") or "")) == 6
            and str(row.get("ticker") or "").isdigit()
            and _safe_float(row.get("target_weight")) > 0
        ]
        rebalance_history_rows, rebalance_history_hint = (
            self._build_rebalance_history_context(
                current_targets=current_targets_for_history,
                current_cash_weight=decided_target_cash_weight,
            )
        )

        advice_seed_json = {
            "header": {
                "mode": "PAPER_DIRECT",
                "date_kst": date_kst,
                "time_kst": time_kst,
                "as_of": f"{date_kst} {time_kst} KST",
            },
            "market_mood": market_mood,
            "portfolio": {
                **portfolio_status,
                "total_krw": portfolio_status.get("total"),
                "cash_krw": portfolio_status.get("cash"),
                "positions": portfolio_positions,
                "concentration_summary": portfolio_status.get("concentration"),
            },
            "action_plan": {
                "trades": action_trades,
                "rebalance_table_rows": rebalance_rows,
                "notes": [
                    _clean_line(item, 120)
                    for item in list(rebalance.get("notes") or [])
                    if _clean_line(item, 120)
                ][:5]
                + [
                    f"target_cash_weight {decided_target_cash_weight*100:.1f}% (research_adaptive)",
                    rebalance_history_hint,
                ],
            },
            "model_portfolio": {
                "targets": model_targets,
                "target_cash_weight": round(decided_target_cash_weight, 4),
            },
            "evidence_coverage": {
                "reports_used_count": reports_covered,
                "tickers_covered": tickers_covered,
                "filter_rejects": filter_rejects,
                "gaps": gaps,
                "next_actions": next_actions,
                "name_mapping_failures": name_mapping_failures,
                "rebalance_history": rebalance_history_rows,
            },
            "actions": action_trades,
            "trades": action_trades,
            "ideas": selected_ideas,
            "data_status": {
                "reports_used_count": reports_covered,
                "tickers_covered": tickers_covered,
                "filter_rejects": filter_rejects,
                "gaps": gaps,
                "next_actions": next_actions,
                "name_mapping_failures": name_mapping_failures,
            },
            "notes": ["TRADING — 실제 매매는 사용자 책임"],
        }

        used_candidates = [
            {
                "type": str(item.get("action") or "HOLD"),
                "ticker": str(item.get("ticker") or ""),
                "name": str(item.get("name") or ""),
            }
            for item in action_trades
        ]

        dedupe_key = {
            "actions": [
                {
                    "action": str(item.get("action") or "HOLD"),
                    "ticker": str(item.get("ticker") or ""),
                    "size": item.get("size"),
                    "status": str(item.get("status") or "PROPOSED"),
                }
                for item in action_trades
            ],
            "ideas": [str(item.get("ticker") or "") for item in selected_ideas[:6]],
        }

        return {
            "strategy_spec": self._build_strategy_spec(
                target_cash_weight=decided_target_cash_weight
            ),
            "top_n": int(self.config.top_n),
            "options_count": int(self.config.option_count),
            "triggers_count": int(self.config.trigger_count),
            "candidates": self._select_top_n(
                self._build_candidates(snapshot, enriched), top_n=self.config.top_n
            ),
            "rebalance_options": rebalance_rows,
            "playbook": self._build_playbook(snapshot=snapshot, enriched=enriched),
            "used_candidates": used_candidates,
            "gaps": gaps,
            "advice_seed_json": advice_seed_json,
            "dedupe_key": dedupe_key,
        }

    async def _render_with_llm_json(
        self, *, snapshot: dict[str, Any], pack: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not self.llm_bridge.ready:
            return None
        seed = self._render_json_fallback(pack)
        coverage_obj = seed.get("evidence_coverage")
        recent_history_rows: list[Any] = []
        if isinstance(coverage_obj, dict):
            history_obj = coverage_obj.get("rebalance_history")
            if isinstance(history_obj, list):
                recent_history_rows = history_obj
        payload = {
            "model": self.llm_bridge.resolved_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only one JSON object with keys: header, market_mood, portfolio, action_plan, model_portfolio, evidence_coverage, notes. "
                        "Do not add extra keys. Keep action_plan.trades length exactly 3 and model_portfolio.targets length 5-6 when possible. "
                        "Rewrite wording only; preserve numbers and evidence citations. "
                        "Rebalance target churn must stay low versus recent rebalance history: avoid abrupt target replacement/weight swings unless evidence_coverage clearly justifies it. "
                        "No markdown output."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "Polish portfolio direct-brief JSON while preserving facts.",
                            "as_of": snapshot.get("as_of"),
                            "seed": seed,
                            "rebalance_guidance": {
                                "stability_priority": "high",
                                "instruction": "직전 리밸런싱 히스토리를 근거로 목표 비중 급변을 피하고 완만 조정 우선",
                                "recent_history": recent_history_rows,
                            },
                            "schema": {
                                "header": {
                                    "mode": "PAPER_DIRECT",
                                    "date_kst": "YYYY-MM-DD",
                                    "time_kst": "HH:mm",
                                    "as_of": "YYYY-MM-DD HH:mm KST",
                                },
                                "market_mood": ["...", "...", "..."],
                                "action_plan": {
                                    "trades": [
                                        {
                                            "type": "BUY|SELL|REDUCE|REBALANCE|HOLD",
                                            "status": "PROPOSED|ON_HOLD",
                                            "name": "삼성전자",
                                            "ticker": "005930",
                                            "size": {
                                                "kind": "SHARES|KRW|TARGET_WEIGHT",
                                                "value": 10,
                                            },
                                            "entry": "MARKET(as_of=YYYY-MM-DD HH:mm KST)",
                                            "rationale_bullets": ["...", "..."],
                                            "key_numbers": ["...", "..."],
                                            "risks": ["...", "..."],
                                            "invalidation": ["...", "..."],
                                            "evidence": [
                                                "[브로커, YYYY-MM-DD, p.X]",
                                                "DATA(as_of=...)",
                                            ],
                                        }
                                    ],
                                    "rebalance_table_rows": [
                                        {
                                            "name": "삼성전자",
                                            "ticker": "005930",
                                            "current_weight": 0.42,
                                            "target_weight": 0.20,
                                            "delta_weight": -0.22,
                                            "action": "REDUCE",
                                        }
                                    ],
                                },
                                "model_portfolio": {
                                    "targets": [
                                        {
                                            "name": "삼성전자",
                                            "ticker": "005930",
                                            "target_weight": 0.20,
                                            "rating_consensus": "BUY",
                                            "tp_consensus": 98000,
                                            "upside_pct": 18.2,
                                            "coverage_count": 4,
                                            "last_update": "YYYY-MM-DD",
                                            "why": "...",
                                            "bull_points": ["...", "..."],
                                            "bear_points": ["...", "..."],
                                            "what_to_watch": ["...", "..."],
                                            "evidence": [
                                                "[브로커, YYYY-MM-DD, p.X]",
                                                "[브로커2, YYYY-MM-DD, p.Y]",
                                            ],
                                        }
                                    ]
                                },
                                "portfolio": {
                                    "total": "10,000,000원",
                                    "cash": "1,000,000원",
                                    "holdings_summary": "삼성전자(005930), SK하이닉스(000660)",
                                    "concentration": "최대 비중 28.0% / 정책 상한 20.0%",
                                },
                                "evidence_coverage": {
                                    "reports_used_count": 120,
                                    "tickers_covered": 48,
                                    "filter_rejects": [{"reason": "...", "count": 10}],
                                    "gaps": ["..."],
                                    "next_actions": ["..."],
                                },
                                "notes": ["TRADING — 실제 매매는 사용자 책임"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        result = await self.llm_bridge.complete(payload)
        if not bool(result.get("ok")):
            return None
        text = str(result.get("content") or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        action_plan_obj = parsed.get("action_plan")
        if not isinstance(action_plan_obj, dict):
            return None
        if not isinstance(action_plan_obj.get("trades"), list):
            return None
        model_obj = parsed.get("model_portfolio")
        if not isinstance(model_obj, dict):
            return None
        if not isinstance(model_obj.get("targets"), list):
            return None
        return parsed

    def _render_json_fallback(self, pack: dict[str, Any]) -> dict[str, Any]:
        payload = pack.get("advice_seed_json")
        if isinstance(payload, dict):
            return payload
        now = datetime.now(KST)
        now_as_of = now.strftime("%Y-%m-%d %H:%M KST")
        fallback_actions = [
            self._build_hold_action(
                action="REDUCE",
                as_of=utc_now_iso(),
                reason="제안 보류(근거 부족: 조언 입력 생성 실패)",
                next_action="브릿지 응답/입력 데이터 점검",
            ),
            self._build_hold_action(
                action="BUY",
                as_of=utc_now_iso(),
                reason="제안 보류(근거 부족: 조언 입력 생성 실패)",
                next_action="브릿지 응답/입력 데이터 점검",
            ),
            self._build_hold_action(
                action="REBALANCE",
                as_of=utc_now_iso(),
                reason="제안 보류(근거 부족: 조언 입력 생성 실패)",
                next_action="브릿지 응답/입력 데이터 점검",
            ),
        ]
        return {
            "header": {
                "mode": "PAPER_DIRECT",
                "date_kst": now.strftime("%Y-%m-%d"),
                "time_kst": now.strftime("%H:%M"),
                "as_of": now_as_of,
            },
            "market_mood": [
                f"데이터 기준시각(as_of): {now_as_of}",
                "시장 분위기: 데이터 제한으로 보수적 대응",
                "확정 수치 부족 시 비중 축소/분산 점검 우선",
            ],
            "action_plan": {
                "trades": fallback_actions,
                "rebalance_table_rows": [],
            },
            "actions": fallback_actions,
            "model_portfolio": {"targets": []},
            "ideas": [],
            "portfolio": {
                "total": "-",
                "cash": "-",
                "holdings_summary": "데이터 없음",
                "concentration": "데이터 없음",
            },
            "evidence_coverage": {
                "reports_used_count": 0,
                "tickers_covered": 0,
                "filter_rejects": [],
                "gaps": ["조언 seed 생성 실패"],
                "next_actions": ["브릿지/입력 데이터 점검"],
            },
            "data_status": {
                "reports_used_count": 0,
                "tickers_covered": 0,
                "filter_rejects": [],
                "gaps": ["조언 seed 생성 실패"],
                "next_actions": ["브릿지/입력 데이터 점검"],
            },
            "coverage": {
                "reports_covered": 0,
                "tickers_covered": 0,
                "filter_rejects": [],
                "gaps": ["조언 seed 생성 실패"],
                "next_actions": ["브릿지/입력 데이터 점검"],
            },
            "notes": ["TRADING — 실제 매매는 사용자 책임"],
        }

    def _normalize_action_payload(
        self,
        payload: dict[str, Any] | None,
        *,
        pack: dict[str, Any],
    ) -> dict[str, Any]:
        seed = self._render_json_fallback(pack)
        base = payload if isinstance(payload, dict) else seed

        header_obj_raw = base.get("header")
        header_obj = dict(header_obj_raw) if isinstance(header_obj_raw, dict) else {}
        seed_header_obj_raw = seed.get("header")
        seed_header_obj = (
            dict(seed_header_obj_raw) if isinstance(seed_header_obj_raw, dict) else {}
        )
        header: dict[str, Any] = {
            "mode": _clean_line(header_obj.get("mode"), 24)
            or _clean_line(seed_header_obj.get("mode"), 24)
            or "PAPER_DIRECT",
            "date_kst": _clean_line(header_obj.get("date_kst"), 10)
            or _clean_line(seed_header_obj.get("date_kst"), 10),
            "time_kst": _clean_line(header_obj.get("time_kst"), 5)
            or _clean_line(seed_header_obj.get("time_kst"), 5),
            "as_of": _clean_line(header_obj.get("as_of"), 24)
            or _clean_line(seed_header_obj.get("as_of"), 24),
        }

        allowed_actions = {"BUY", "SELL", "REDUCE", "REBALANCE", "HOLD"}
        expected_slots = ["REDUCE", "BUY", "REBALANCE"]
        base_action_plan_raw = base.get("action_plan")
        base_action_plan = (
            dict(base_action_plan_raw) if isinstance(base_action_plan_raw, dict) else {}
        )
        seed_action_plan_raw = seed.get("action_plan")
        seed_action_plan = (
            dict(seed_action_plan_raw) if isinstance(seed_action_plan_raw, dict) else {}
        )
        base_actions = [
            row
            for row in list(
                base_action_plan.get("trades")
                or base.get("actions")
                or base.get("trades")
                or []
            )
            if isinstance(row, dict)
        ]
        seed_actions = [
            row
            for row in list(
                seed_action_plan.get("trades")
                or seed.get("actions")
                or seed.get("trades")
                or []
            )
            if isinstance(row, dict)
        ]

        normalized_actions: list[dict[str, Any]] = []
        on_hold_count = 0
        for idx in range(3):
            source = (
                base_actions[idx]
                if idx < len(base_actions)
                else (seed_actions[idx] if idx < len(seed_actions) else {})
            )
            fallback = seed_actions[idx] if idx < len(seed_actions) else {}

            action = (
                _clean_line(source.get("type"), 12).upper()
                or _clean_line(fallback.get("type"), 12).upper()
                or _clean_line(source.get("action"), 12).upper()
                or _clean_line(fallback.get("action"), 12).upper()
                or expected_slots[idx]
            )
            if action not in allowed_actions:
                action = expected_slots[idx]

            status = (
                _clean_line(source.get("status"), 12).upper()
                or _clean_line(fallback.get("status"), 12).upper()
                or "PROPOSED"
            )
            if status not in {"PROPOSED", "ON_HOLD"}:
                status = "PROPOSED"

            ticker = _clean_line(source.get("ticker"), 12) or _clean_line(
                fallback.get("ticker"), 12
            )
            name = self._resolve_security_name(
                ticker,
                _clean_line(source.get("name"), 40),
                _clean_line(fallback.get("name"), 40),
            )

            size_raw_obj = source.get("size")
            if not isinstance(size_raw_obj, dict):
                size_raw_obj = fallback.get("size")
            size_obj: dict[str, Any] = (
                dict(size_raw_obj) if isinstance(size_raw_obj, dict) else {}
            )
            size: dict[str, Any] = {
                "kind": _clean_line(size_obj.get("kind"), 16) or "SHARES",
                "value": size_obj.get("value")
                if size_obj.get("value") is not None
                else 0,
            }

            rationale = (
                [
                    _clean_line(row, 100)
                    for row in list(source.get("rationale_bullets") or [])
                    if _clean_line(row, 100)
                ][:2]
                or [
                    _clean_line(row, 100)
                    for row in list(fallback.get("rationale_bullets") or [])
                    if _clean_line(row, 100)
                ][:2]
                or ["제안 보류(근거 부족)"]
            )

            key_numbers = (
                [
                    _clean_line(row, 90)
                    for row in list(source.get("key_numbers") or [])
                    if _clean_line(row, 90)
                ][:2]
                or [
                    _clean_line(row, 90)
                    for row in list(fallback.get("key_numbers") or [])
                    if _clean_line(row, 90)
                ][:2]
                or ["근거 부족"]
            )

            risks = (
                [
                    _clean_line(row, 90)
                    for row in list(source.get("risks") or [])
                    if _clean_line(row, 90)
                ][:2]
                or [
                    _clean_line(row, 90)
                    for row in list(fallback.get("risks") or [])
                    if _clean_line(row, 90)
                ][:2]
                or ["근거 부족"]
            )

            invalidation = (
                [
                    _clean_line(row, 90)
                    for row in list(source.get("invalidation") or [])
                    if _clean_line(row, 90)
                ][:2]
                or [
                    _clean_line(row, 90)
                    for row in list(fallback.get("invalidation") or [])
                    if _clean_line(row, 90)
                ][:2]
                or ["근거 보강 시 재평가"]
            )

            evidence = [
                _clean_line(row, 70)
                for row in list(source.get("evidence") or [])
                if _clean_line(row, 70)
            ][:3]
            if not evidence:
                evidence = [
                    _clean_line(row, 70)
                    for row in list(fallback.get("evidence") or [])
                    if _clean_line(row, 70)
                ][:3]
            if not evidence:
                evidence = ["근거 부족"]

            has_report = any(
                str(item).startswith("[") and "p." in str(item) for item in evidence
            )
            has_data = any(str(item).startswith("DATA(") for item in evidence)
            if action in {"BUY", "SELL", "REDUCE", "REBALANCE"} and status != "ON_HOLD":
                if not (has_report and has_data):
                    status = "ON_HOLD"
                    rationale = ["제안 보류(근거 부족: 리포트+시세 근거 불충분)"]
                    key_numbers = ["다음 액션: 인덱싱/티커매핑/시세 근거 점검"]
                    risks = ["근거 부족"]
                    invalidation = ["근거 보강 시 재평가"]
                    evidence = ["근거 부족"]

            if action == "HOLD" or status == "ON_HOLD":
                on_hold_count += 1

            normalized_actions.append(
                {
                    "type": action,
                    "action": action,
                    "status": status,
                    "name": name,
                    "ticker": ticker,
                    "size": size,
                    "entry": _clean_line(source.get("entry"), 42)
                    or _clean_line(fallback.get("entry"), 42)
                    or "MARKET(as_of=KST)",
                    "rationale_bullets": rationale,
                    "key_numbers": key_numbers,
                    "risks": risks,
                    "invalidation": invalidation,
                    "evidence": evidence,
                }
            )

        if on_hold_count > 1:
            seen_hold = 0
            for idx, row in enumerate(normalized_actions):
                if str(row.get("status") or "") != "ON_HOLD":
                    continue
                seen_hold += 1
                if seen_hold <= 1:
                    continue
                row["status"] = "PROPOSED"
                keep_action = str(row.get("action") or "").upper()
                if keep_action not in allowed_actions:
                    keep_action = expected_slots[idx]
                row["action"] = keep_action
                row["type"] = keep_action
                row["rationale_bullets"] = ["정책 기반 실행안(추가 리포트 확인 병행)"]
                row["key_numbers"] = ["다음 액션: 장중 데이터로 실행 강도 조정"]
                row["evidence"] = [
                    next(
                        (
                            str(item)
                            for idea in list(base.get("ideas") or [])
                            if isinstance(idea, dict)
                            for item in list(idea.get("evidence") or [])
                            if str(item).startswith("[") and "p." in str(item)
                        ),
                        "[근거부족, -, p.?]",
                    ),
                    next(
                        (
                            str(item)
                            for act in normalized_actions
                            for item in list(act.get("evidence") or [])
                            if str(item).startswith("DATA(")
                        ),
                        f"DATA(as_of={header.get('as_of') or 'KST'})",
                    ),
                ]

        base_ideas = [
            row for row in list(base.get("ideas") or []) if isinstance(row, dict)
        ]
        seed_ideas = [
            row for row in list(seed.get("ideas") or []) if isinstance(row, dict)
        ]
        idea_reject: Counter[str] = Counter()
        ideas: list[dict[str, Any]] = []
        seen_tickers: set[str] = set()

        def try_add_idea(row: dict[str, Any]) -> None:
            ticker = _clean_line(row.get("ticker"), 12)
            if not ticker:
                idea_reject["ticker_missing"] += 1
                return
            if ticker in seen_tickers:
                idea_reject["duplicate_ticker"] += 1
                return
            name = self._resolve_security_name(ticker, _clean_line(row.get("name"), 40))
            evidence = [
                _clean_line(item, 70)
                for item in list(row.get("evidence") or [])
                if _clean_line(item, 70)
            ]
            evidence = list(dict.fromkeys(evidence))
            if len(evidence) < 2:
                idea_reject["idea_evidence_short"] += 1
                return
            seen_tickers.add(ticker)
            ideas.append(
                {
                    "name": name,
                    "ticker": ticker,
                    "rating_consensus": _clean_line(row.get("rating_consensus"), 16)
                    or "UNKNOWN",
                    "tp_consensus": _safe_int(row.get("tp_consensus")),
                    "upside_pct": round(_safe_float(row.get("upside_pct")), 1),
                    "coverage_count": _safe_int(row.get("coverage_count")),
                    "last_update": _clean_line(row.get("last_update"), 10),
                    "bull_points": [
                        _clean_line(item, 90)
                        for item in list(row.get("bull_points") or [])
                        if _clean_line(item, 90)
                    ][:2],
                    "bear_points": [
                        _clean_line(item, 90)
                        for item in list(row.get("bear_points") or [])
                        if _clean_line(item, 90)
                    ][:2],
                    "what_to_watch": [
                        _clean_line(item, 90)
                        for item in list(row.get("what_to_watch") or [])
                        if _clean_line(item, 90)
                    ][:2],
                    "target_weight": round(_safe_float(row.get("target_weight")), 4),
                    "why": _clean_line(row.get("why"), 110)
                    or "리서치 컨센서스 기반 모델 포트 편입",
                    "evidence": evidence[:2],
                    "base_score": _safe_float(row.get("base_score")),
                }
            )

        for row in base_ideas:
            if len(ideas) >= 6:
                break
            try_add_idea(row)
        for row in seed_ideas:
            if len(ideas) >= 6:
                break
            try_add_idea(row)

        coverage_obj_raw = base.get("evidence_coverage")
        if not isinstance(coverage_obj_raw, dict):
            coverage_obj_raw = base.get("data_status")
        coverage_obj: dict[str, Any] = (
            dict(coverage_obj_raw) if isinstance(coverage_obj_raw, dict) else {}
        )
        seed_coverage_obj_raw = seed.get("evidence_coverage")
        if not isinstance(seed_coverage_obj_raw, dict):
            seed_coverage_obj_raw = seed.get("data_status")
        seed_coverage_obj: dict[str, Any] = (
            dict(seed_coverage_obj_raw)
            if isinstance(seed_coverage_obj_raw, dict)
            else {}
        )
        reports_covered = _safe_int(
            coverage_obj.get("reports_used_count")
            or seed_coverage_obj.get("reports_used_count")
        )
        tickers_covered = _safe_int(
            coverage_obj.get("tickers_covered")
            or seed_coverage_obj.get("tickers_covered")
        )
        filter_rows = [
            row
            for row in list(
                coverage_obj.get("filter_rejects")
                or seed_coverage_obj.get("filter_rejects")
                or []
            )
            if isinstance(row, dict)
        ]
        reject_sum: Counter[str] = Counter()
        for row in filter_rows:
            reason = _clean_line(row.get("reason"), 40) or "unknown"
            reject_sum[reason] += _safe_int(row.get("count")) or 1
        for reason, count in idea_reject.items():
            reject_sum[reason] += count
        filter_rejects = [
            {"reason": reason, "count": count}
            for reason, count in reject_sum.most_common(3)
        ]

        gaps = [
            _clean_line(row, 100)
            for row in list(
                coverage_obj.get("gaps") or seed_coverage_obj.get("gaps") or []
            )
            if _clean_line(row, 100)
        ]
        next_actions = [
            _clean_line(row, 100)
            for row in list(
                coverage_obj.get("next_actions")
                or seed_coverage_obj.get("next_actions")
                or []
            )
            if _clean_line(row, 100)
        ]
        if len(ideas) < 5:
            gaps.append(f"신규 아이디어 최소 개수 미충족({len(ideas)}개)")
            next_actions.append("lookback/rating/티커 매핑 완화 후 재시도")

        mood_lines = [
            _clean_line(row, 110)
            for row in list(base.get("market_mood") or seed.get("market_mood") or [])
            if _clean_line(row, 110)
        ]
        if len(mood_lines) < 3:
            mood_as_of = _clean_line(header.get("as_of"), 24)
            if not mood_as_of:
                date_text = _clean_line(header.get("date_kst"), 10)
                time_text = _clean_line(header.get("time_kst"), 5)
                mood_as_of = f"{date_text} {time_text} KST".strip()
            mood_lines.extend(
                [
                    f"데이터 기준시각(as_of): {mood_as_of}",
                    "시장 분위기: 데이터 제한 시 보수적 대응",
                    "변동성 확대 구간에서는 비중/손절 규칙 우선",
                ]
            )
        mood_lines = mood_lines[:6]

        portfolio_obj_raw = base.get("portfolio")
        portfolio_obj = (
            dict(portfolio_obj_raw) if isinstance(portfolio_obj_raw, dict) else {}
        )
        seed_portfolio_obj_raw = seed.get("portfolio")
        seed_portfolio_obj = (
            dict(seed_portfolio_obj_raw)
            if isinstance(seed_portfolio_obj_raw, dict)
            else {}
        )
        portfolio = {
            "total": _clean_line(portfolio_obj.get("total"), 40)
            or _clean_line(seed_portfolio_obj.get("total"), 40)
            or "-",
            "cash": _clean_line(portfolio_obj.get("cash"), 40)
            or _clean_line(seed_portfolio_obj.get("cash"), 40)
            or "-",
            "holdings_summary": _clean_line(portfolio_obj.get("holdings_summary"), 100)
            or _clean_line(seed_portfolio_obj.get("holdings_summary"), 100)
            or "데이터 없음",
            "concentration": _clean_line(portfolio_obj.get("concentration"), 100)
            or _clean_line(seed_portfolio_obj.get("concentration"), 100)
            or "데이터 없음",
            "total_krw": _clean_line(portfolio_obj.get("total_krw"), 40)
            or _clean_line(seed_portfolio_obj.get("total_krw"), 40)
            or _clean_line(portfolio_obj.get("total"), 40)
            or _clean_line(seed_portfolio_obj.get("total"), 40)
            or "-",
            "cash_krw": _clean_line(portfolio_obj.get("cash_krw"), 40)
            or _clean_line(seed_portfolio_obj.get("cash_krw"), 40)
            or _clean_line(portfolio_obj.get("cash"), 40)
            or _clean_line(seed_portfolio_obj.get("cash"), 40)
            or "-",
            "positions": [
                row
                for row in list(
                    portfolio_obj.get("positions")
                    or seed_portfolio_obj.get("positions")
                    or []
                )
                if isinstance(row, dict)
            ][:8],
            "concentration_summary": _clean_line(
                portfolio_obj.get("concentration_summary"), 100
            )
            or _clean_line(seed_portfolio_obj.get("concentration_summary"), 100)
            or _clean_line(portfolio_obj.get("concentration"), 100)
            or _clean_line(seed_portfolio_obj.get("concentration"), 100)
            or "데이터 없음",
        }

        rebalance_table_rows = [
            row
            for row in list(
                base_action_plan.get("rebalance_table_rows")
                or seed_action_plan.get("rebalance_table_rows")
                or []
            )
            if isinstance(row, dict)
        ]
        model_targets = [
            row
            for row in list(
                (
                    (base.get("model_portfolio") or {})
                    if isinstance(base.get("model_portfolio"), dict)
                    else {}
                ).get("targets")
                or (
                    (seed.get("model_portfolio") or {})
                    if isinstance(seed.get("model_portfolio"), dict)
                    else {}
                ).get("targets")
                or ideas
            )
            if isinstance(row, dict)
        ][:6]

        name_fail_count = sum(
            1
            for row in normalized_actions
            if str(row.get("name") or "").startswith("UNKNOWN_NAME")
        ) + sum(
            1 for row in ideas if str(row.get("name") or "").startswith("UNKNOWN_NAME")
        )
        if name_fail_count > 0:
            gaps.append(f"이름 매핑 실패 {name_fail_count}건")
            next_actions.append(
                "ticker->name 매핑 소스(pykrx/리포트 메타/시세명) 재시도"
            )

        notes = [
            _clean_line(row, 120)
            for row in list(base.get("notes") or [])
            if _clean_line(row, 120)
        ]
        if not notes:
            notes = [
                _clean_line(row, 120)
                for row in list(seed.get("notes") or [])
                if _clean_line(row, 120)
            ]
        if not notes:
            notes = ["TRADING — 실제 매매는 사용자 책임"]

        return {
            "header": header,
            "market_mood": mood_lines,
            "action_plan": {
                "trades": normalized_actions,
                "rebalance_table_rows": rebalance_table_rows,
            },
            "actions": normalized_actions,
            "trades": normalized_actions,
            "ideas": ideas,
            "model_portfolio": {
                "targets": model_targets,
            },
            "portfolio": portfolio,
            "evidence_coverage": {
                "reports_used_count": reports_covered,
                "tickers_covered": tickers_covered,
                "filter_rejects": filter_rejects,
                "gaps": list(dict.fromkeys(gaps))[:6],
                "next_actions": list(dict.fromkeys(next_actions))[:6],
            },
            "data_status": {
                "reports_used_count": reports_covered,
                "tickers_covered": tickers_covered,
                "filter_rejects": filter_rejects,
                "gaps": list(dict.fromkeys(gaps))[:6],
                "next_actions": list(dict.fromkeys(next_actions))[:6],
            },
            "coverage": {
                "reports_covered": reports_covered,
                "tickers_covered": tickers_covered,
                "filter_rejects": filter_rejects,
                "gaps": list(dict.fromkeys(gaps))[:6],
                "next_actions": list(dict.fromkeys(next_actions))[:6],
            },
            "notes": notes,
        }
