from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


DashboardProvider = Callable[[], dict[str, Any]]
TRADING_FOOTER = "실거래 판단용입니다. 주문은 HERMES 안전 게이트와 블록 규칙을 통과한 경우에만 실행됩니다."
VALIDATION_GATE_LABELS = {
    "clear": "검증 통과",
    "blocked_by_validation": "검증 차단",
    "validation_error": "검증 오류",
    "validation_incomplete": "19개 검증 미완성",
    "validation_missing": "검증 결과 없음",
    "validation_normal": "Normal 단계",
    "validation_probe": "Probe 단계",
    "validation_research_only": "Research 단계",
    "validation_stale": "검증 오래됨",
}
VALIDATION_GATE_REASON_LABELS = {
    "live_authority_error": "Live Authority 오류로 신규 리스크 중단",
    "live_authority_budget_zero": "신규 리스크 예산 0",
    "live_authority_risk_governor:halt_new_risk": "리스크 governor가 신규 진입 중단",
    "no_trading_validation_readiness": "검증 readiness 없음",
    "validation_readiness_normal_not_scale_ready": "Normal 단계라 스케일업 보류",
    "validation_readiness_probe_not_scale_ready": "Probe 단계라 대기 진입 중심",
    "validation_readiness_research_only": "Research 단계라 공격 확대 보류",
}
VALIDATION_READINESS_LABELS = {
    "blocked_by_validation": "검증 차단",
    "normal": "Normal 단계",
    "probe": "Probe 단계",
    "research_only": "Research 단계",
    "scale_ready": "스케일 준비",
}
RISK_GOVERNOR_LABELS = {
    "de_risk": "리스크 축소",
    "halt_new_risk": "신규 리스크 중단",
    "normal": "정상",
    "risk_off": "리스크 오프",
}
RISK_GOVERNOR_SOURCE_LABELS = {
    "kelly_sizing": "Kelly sizing",
    "mdd_limit": "MDD",
    "risk_of_ruin": "파산확률",
    "ruin_profile": "파산확률",
}
LOSS_COOLDOWN_ACTION_LABELS = {
    "deprioritize_until_revalidated": "재검증 전 우선순위 하향",
    "do_not_scale_or_create_live_entry_without_new_evidence": "신규 확대 금지",
}
REPAIR_EXECUTION_STATUS_LABELS = {
    "executed": "실행됨",
    "observed_external_runner": "외부 러너 확인",
    "queued": "대기",
    "queued_external_runner": "외부 러너 대기",
    "queued_validation_refresh": "검증 갱신 대기",
    "running": "실행 중",
    "error": "오류",
    "failed": "실패",
}


def _fmt_krw(value: float | int | None) -> str:
    amount = int(float(value or 0))
    return f"{amount:,} KRW"


def _fmt_signed_krw(value: float | int | None) -> str:
    amount = int(float(value or 0))
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(amount):,} KRW"


def _fmt_krw_won(value: float | int | None) -> str:
    amount = int(float(value or 0))
    return f"{amount:,} 원"


def _fmt_signed_krw_won(value: float | int | None) -> str:
    amount = int(float(value or 0))
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{abs(amount):,} 원"


def _fmt_pct(value: float | int | None, digits: int = 2) -> str:
    return f"{float(value or 0):.{digits}f}%"


def _fmt_num(value: float | int | None) -> str:
    return f"{int(float(value or 0)):,}"


def _fmt_float(value: Any, digits: int = 1) -> str:
    return f"{float(value or 0):.{digits}f}"


def _fmt_multiplier(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def _validation_gate_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return "-"
    return VALIDATION_GATE_LABELS.get(key, key.replace("_", " "))


def _validation_gate_reason(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "-"
    if raw in VALIDATION_GATE_REASON_LABELS:
        return VALIDATION_GATE_REASON_LABELS[raw]
    if raw.startswith("live_authority_risk_governor:"):
        action = raw.split(":", 1)[1]
        return f"리스크 governor: {_risk_governor_label(action)}"
    incomplete = re.search(r"discipline_count=(\d+),\s*expected=(\d+)", raw)
    if incomplete:
        return f"검증 항목 수 부족: {incomplete.group(1)}/{incomplete.group(2)}"
    fail_count = re.search(r"fail_count=(\d+)", raw)
    if "blocked_by_validation" in raw and fail_count:
        return f"실패 항목 {fail_count.group(1)}개"
    return raw.replace("_", " ")


def _validation_readiness_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return "-"
    return VALIDATION_READINESS_LABELS.get(key, key.replace("_", " "))


def _risk_governor_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return "-"
    return RISK_GOVERNOR_LABELS.get(key, key.replace("_", " "))


def _risk_governor_source_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return ""
    return RISK_GOVERNOR_SOURCE_LABELS.get(key, key.replace("_", " "))


def _loss_cooldown_action_label(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        return "-"
    return LOSS_COOLDOWN_ACTION_LABELS.get(key, key.replace("_", " "))


def _repair_execution_status_label(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        return "-"
    return REPAIR_EXECUTION_STATUS_LABELS.get(key, key.replace("_", " "))


def _fmt_weight_pct(value: Any) -> str:
    numeric = float(value or 0)
    if abs(numeric) <= 1:
        numeric *= 100
    return f"{numeric:.0f}%"


def _horizon_label(value: Any) -> str:
    return {
        "cash": "현금",
        "short": "단기",
        "mid": "중기",
        "long": "장기",
        "core_etf": "ETF/Core",
    }.get(str(value or ""), str(value or "-"))


def _fmt_rate(value: float | int | None) -> str:
    return f"{float(value or 0):,.2f}"


def _fmt_kst(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "-"
    kst = dt.astimezone(ZoneInfo("Asia/Seoul"))
    return kst.strftime("%Y-%m-%d %H:%M:%S")


def _compact_number(value: float | int | None, digits: int = 1) -> str:
    num = float(value or 0)
    abs_num = abs(num)
    if abs_num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.{digits}f}B"
    if abs_num >= 1_000_000:
        return f"{num / 1_000_000:.{digits}f}M"
    if abs_num >= 1_000:
        return f"{num / 1_000:.{digits}f}K"
    if float(num).is_integer():
        return str(int(num))
    return f"{num:.2f}".rstrip("0").rstrip(".")


def _strip_bot_suffix(command_token: str) -> str:
    core = command_token[1:] if command_token.startswith("/") else command_token
    return core.split("@", 1)[0].strip().lower()


def _is_krx_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _clean_symbol_name(value: Any, *, symbol: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    code = str(symbol or "").strip()
    if not text or text == code or _is_krx_symbol(text):
        return ""
    if text in {"정보", "투자", "종목", "종목명", "코드", "리포트", "기업"}:
        return ""
    if "<" in text or ">" in text:
        return ""
    return text


def _symbol_label(row: dict[str, Any], *, fallback_symbol: str = "") -> str:
    symbol = str(row.get("symbol") or fallback_symbol or "").strip()
    name = _clean_symbol_name(
        row.get("name") or row.get("asset_name") or row.get("company_name"),
        symbol=symbol,
    )
    if name and symbol:
        return f"{name} ({symbol})"
    return name or symbol or "-"


def _strategy_horizon(row: dict[str, Any], key: str) -> dict[str, Any]:
    suitability = row.get("suitability") if isinstance(row.get("suitability"), dict) else {}
    horizon = suitability.get(key) if isinstance(suitability.get(key), dict) else {}
    return {
        "score": int(float(horizon.get("score") or row.get("score") or 0)),
        "grade": str(horizon.get("grade") or "-"),
        "drivers": list(horizon.get("drivers") or []) if isinstance(horizon.get("drivers"), list) else [],
        "risks": list(horizon.get("risks") or []) if isinstance(horizon.get("risks"), list) else [],
    }


def _strategy_suitability_line(row: dict[str, Any]) -> str:
    balanced = _strategy_horizon(row, "balanced")
    short = _strategy_horizon(row, "short_term")
    mid = _strategy_horizon(row, "mid_term")
    long = _strategy_horizon(row, "long_term")
    return (
        f"균형 {balanced['grade']} {balanced['score']} · "
        f"단기 {short['grade']} / 중기 {mid['grade']} / 장기 {long['grade']}"
    )


def _strategy_warning_line(row: dict[str, Any]) -> str:
    warnings = [
        str(item)
        for item in list(row.get("data_warnings") or [])
        if str(item).strip()
    ][:4]
    if not warnings:
        coverage = row.get("data_coverage") if isinstance(row.get("data_coverage"), dict) else {}
        missing = [
            str(item)
            for item in list(coverage.get("missing") or [])
            if str(item).strip()
        ][:3]
        if missing:
            warnings.append(f"미수집 {', '.join(missing)}")
    identity = row.get("identity_status") if isinstance(row.get("identity_status"), dict) else {}
    if str(identity.get("status") or "") and str(identity.get("status") or "") != "ok":
        warnings.insert(0, str(identity.get("label") or "종목명 검증 필요"))
    return " · ".join(list(dict.fromkeys(warnings))[:4])


def _market_action_label(value: Any) -> str:
    labels = {
        "hold": "유지",
        "watch_add": "추가 관심",
        "avoid_add": "추가 보류",
        "trim_watch": "비중 점검",
        "risk_check": "리스크 관리",
        "new_watch": "신규 관심",
    }
    key = str(value or "").strip()
    return labels.get(key, key or "-")


def _char_display_width(ch: str) -> int:
    return 1


def _display_width(text: str) -> int:
    return sum(_char_display_width(ch) for ch in text)


def _truncate_display(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text

    if width <= 3:
        out: list[str] = []
        used = 0
        for ch in text:
            w = _char_display_width(ch)
            if used + w > width:
                break
            out.append(ch)
            used += w
        return "".join(out)

    target = width - 3
    out = []
    used = 0
    for ch in text:
        w = _char_display_width(ch)
        if used + w > target:
            break
        out.append(ch)
        used += w
    return "".join(out) + "..."


class TelegramCLI:
    def __init__(
        self,
        dashboard_provider: DashboardProvider,
    ) -> None:
        self.dashboard_provider = dashboard_provider

    def parse(self, text: str) -> tuple[str, list[str]]:
        raw = (text or "").strip()
        if not raw:
            return "", []
        tokens = raw.split()
        if not tokens or not tokens[0].startswith("/"):
            return "", []
        command = _strip_bot_suffix(tokens[0])
        return command, tokens[1:]

    def execute(self, text: str) -> tuple[bool, str]:
        command, args = self.parse(text)
        if not command:
            return False, ""

        if command in {"start", "help"}:
            return True, self._help_text()
        dashboard = self.dashboard_provider()
        return self._execute_parsed(command, args, dashboard)

    def handle_text(self, text: str) -> tuple[bool, str]:
        return self.execute(text)

    def execute_with_dashboard(self, text: str, dashboard: dict[str, Any]) -> tuple[bool, str]:
        command, args = self.parse(text)
        if not command:
            return False, ""
        return self._execute_parsed(command, args, dashboard)

    def _execute_parsed(self, command: str, args: list[str], dashboard: dict[str, Any]) -> tuple[bool, str]:
        if command in {"start", "help"}:
            return True, self._help_text()
        if command == "status":
            return True, self._status_text(dashboard)
        if command in {"venues", "balance"}:
            return True, self._venues_text(dashboard, args)
        if command in {"upbit", "bithumb", "bth", "binance", "binancef", "binf", "futures", "krx", "kr2", "us"}:
            return True, self._venues_text(dashboard, [command])
        if command == "sessions":
            return True, self._sessions_text(dashboard)
        if command == "session":
            return True, self._session_detail_text(dashboard, args)
        if command in {"ask", "strategy", "bot"}:
            return True, self._strategy_usage_text(args)
        if command in {"watchlist", "why"}:
            return True, self._strategy_usage_text(args)
        if command in {"market", "judge", "why-now"}:
            return True, self._market_usage_text(args)
        if command in {"memory", "mindset", "journal", "reflect", "why-block"}:
            return True, self._memory_usage_text(args)
        if command in {"llm-usage", "llm_usage"}:
            payload = dashboard.get("llm_usage") if isinstance(dashboard.get("llm_usage"), dict) else {}
            return True, self.llm_usage_text(payload)
        if command in {"live", "authority"}:
            payload = dashboard.get("live_authority") if isinstance(dashboard.get("live_authority"), dict) else {}
            return True, self.live_authority_text(payload)

        return (
            True,
            "알 수 없는 명령어입니다.\n"
            "지원 명령어는 /help 에서 확인하세요.",
        )

    def _help_text(self) -> str:
        return "\n".join(
            [
                "HERMES BOT Telegram CLI",
                "",
                "/help - 명령어 목록",
                "/status - 전체 자산/손익 요약",
                "/venues - 거래소별 잔고 요약표",
                "/balance <venue> - 특정 거래소 잔고 상세표",
                "/upbit /bithumb /binance /binancef /krx /kr2 /us - 거래소 바로가기",
                "/sessions - 전체 세션 요약",
                "/session <session_id> - 특정 세션 상세",
                "/ask <질문> - 전략 인텔리전스에 질문",
                "/strategy <질문> - 다음 거래일 후보/시장 판단",
                "/bot <질문> - 매매봇 전략 질문",
                "/watchlist - 전략 인텔리전스 관심 후보",
                "/why <종목코드> - 후보 근거/반론 상세",
                "/market - 최신 장중 시세/판단 요약",
                "/judge - 장중 LLM 판단 실행",
                "/why-now <종목코드> - 현재 시세와 계좌 기준 상세",
                "/memory - 활성 메모리/운용 원칙 요약",
                "/mindset - 오늘 장전 마음가짐",
                "/journal - 오늘 메모리 저널",
                "/reflect - 최근 블록 거래 반성 실행",
                "/weekly-review - 주간 운용 반성 실행",
                "/monthly-review - 월간 운용 반성 실행",
                "/policy - 최근 정책 개정안",
                "/why-block <block_id> - 블록별 기억/교훈",
                "/llm-usage - 오늘 LLM 호출/토큰 사용량",
                "/live - KIS/Binance 실전 권한 상태",
            ]
        )

    def live_authority_text(self, payload: dict[str, Any]) -> str:
        venues = payload.get("venues") if isinstance(payload.get("venues"), dict) else {}
        lines = ["쥬 Live Authority"]
        for venue in ("kis", "binance"):
            row = venues.get(venue) if isinstance(venues.get(venue), dict) else {}
            scorecards = row.get("scorecards") if isinstance(row.get("scorecards"), list) else []
            validation_gate = (
                row.get("validation_gate")
                if isinstance(row.get("validation_gate"), dict)
                else {}
            )
            trading_validation = (
                row.get("trading_validation")
                if isinstance(row.get("trading_validation"), dict)
                else {}
            )
            validation_summary = (
                trading_validation.get("summary")
                if isinstance(trading_validation.get("summary"), dict)
                else {}
            )
            validation_payload = (
                trading_validation.get("payload")
                if isinstance(trading_validation.get("payload"), dict)
                else trading_validation
            )
            lines.append(
                f"- {venue.upper()}: {row.get('live_grade') or row.get('status') or '-'} "
                f"· 배수 {_fmt_multiplier(row.get('max_budget_multiplier'))}x "
                f"· 스케일업 {'허용' if row.get('allow_scale_up') else '보류'} "
                f"· 카드 {row.get('scorecard_count') or len(scorecards)}"
            )
            gate_status = str(validation_gate.get("status") or "").strip()
            readiness = str(
                validation_gate.get("readiness")
                or validation_summary.get("readiness")
                or ""
            ).strip()
            if gate_status or readiness or validation_summary:
                reason = str(validation_gate.get("reason") or "").strip()
                discipline_count = (
                    validation_gate.get("discipline_count")
                    or validation_payload.get("discipline_count")
                )
                expected_discipline_count = (
                    validation_gate.get("expected_discipline_count")
                    or validation_payload.get("expected_discipline_count")
                    or 19
                )
                discipline_text = (
                    f" · 검증수 {_fmt_num(discipline_count)}/{_fmt_num(expected_discipline_count)}"
                    if discipline_count not in (None, "")
                    else ""
                )
                validation_line = (
                    f"  검증 {_validation_gate_label(gate_status)}"
                    f" · readiness {_validation_readiness_label(readiness)}"
                    f"{discipline_text}"
                    f" · pass {_fmt_num(validation_summary.get('pass_count'))}"
                    f" / warn {_fmt_num(validation_summary.get('warn_count'))}"
                    f" / fail {_fmt_num(validation_gate.get('fail_count') or validation_summary.get('fail_count'))}"
                    f" / missing {_fmt_num(validation_summary.get('missing_count'))}"
                )
                if reason:
                    validation_line = f"{validation_line} · {_validation_gate_reason(reason)}"
                lines.append(validation_line)
                validation_passport = (
                    validation_gate.get("validation_passport")
                    if isinstance(validation_gate.get("validation_passport"), dict)
                    else {}
                )
                if validation_passport:
                    actual_count = validation_passport.get("actual_count")
                    expected_count = validation_passport.get("expected_count") or 19
                    row_detail_count = validation_passport.get("row_detail_count")
                    row_detail_complete = bool(
                        validation_passport.get("row_detail_complete")
                    )
                    score = validation_passport.get("score")
                    failed_ids = (
                        validation_passport.get("failed_ids")
                        if isinstance(validation_passport.get("failed_ids"), list)
                        else []
                    )
                    weak_ids = (
                        validation_passport.get("weak_ids")
                        if isinstance(validation_passport.get("weak_ids"), list)
                        else []
                    )
                    passport_status = str(
                        validation_passport.get("status")
                        or validation_passport.get("readiness")
                        or gate_status
                    ).strip()
                    passport_label = (
                        "재검증"
                        if validation_passport.get("requires_revalidation")
                        else _validation_gate_label(passport_status)
                    )
                    failed_text = ", ".join(
                        str(item).strip()
                        for item in failed_ids[:3]
                        if str(item).strip()
                    )
                    weak_text = ", ".join(
                        str(item).strip()
                        for item in weak_ids[:4]
                        if str(item).strip()
                    )
                    detail_parts = [
                        f"{_fmt_num(actual_count)}/{_fmt_num(expected_count)}",
                        (
                            f"row {_fmt_num(row_detail_count)}/{_fmt_num(expected_count)}"
                            f"{'' if row_detail_complete else ' 부분'}"
                            if row_detail_count not in (None, "")
                            else ""
                        ),
                        f"{_fmt_float(score, 1)}점" if score not in (None, "") else "",
                        f"실패 {failed_text}" if failed_text else "",
                        f"취약 {weak_text}" if weak_text else "",
                    ]
                    risk_action = str(
                        validation_passport.get("risk_governor_action") or ""
                    ).strip()
                    if risk_action:
                        detail_parts.append(f"governor {_risk_governor_label(risk_action)}")
                    lines.append(
                        "  검증 여권 "
                        f"{passport_label}"
                        f" · {' · '.join(part for part in detail_parts if part)}"
                    )
                risk_governor = str(
                    validation_gate.get("risk_governor_action") or ""
                ).strip()
                if risk_governor:
                    risk_source = str(
                        validation_gate.get("risk_governor_source") or ""
                    ).strip()
                    source_text = (
                        f" · {_risk_governor_source_label(risk_source)}"
                        if risk_source
                        else ""
                    )
                    lines.append(
                        f"  governor {_risk_governor_label(risk_governor)}{source_text}"
                    )
                failed_disciplines = (
                    validation_gate.get("failed_disciplines")
                    if isinstance(validation_gate.get("failed_disciplines"), list)
                    else []
                )
                failed_labels = [
                    str(row.get("label") or row.get("id") or "").strip()
                    for row in failed_disciplines
                    if isinstance(row, dict)
                    and str(row.get("label") or row.get("id") or "").strip()
                ]
                if failed_labels:
                    lines.append(f"  실패 {', '.join(failed_labels[:4])}")
                capacity = (
                    validation_gate.get("capacity_bottleneck")
                    if isinstance(validation_gate.get("capacity_bottleneck"), dict)
                    else {}
                )
                capacity_symbol = str(capacity.get("tightest_symbol") or "").strip()
                if capacity_symbol:
                    capacity_ratio = capacity.get("min_capacity_ratio")
                    ratio_text = (
                        f" · {float(capacity_ratio or 0):.1f}x"
                        if capacity_ratio is not None
                        else ""
                    )
                    method = str(capacity.get("capacity_method") or "").strip()
                    method_text = f" · {method}" if method else ""
                    lines.append(
                        f"  용량 병목 {capacity_symbol}{ratio_text}{method_text}"
                    )
                attribution = (
                    validation_gate.get("failure_attribution")
                    if isinstance(validation_gate.get("failure_attribution"), dict)
                    else {}
                )
                recovery_focus = (
                    attribution.get("recovery_focus")
                    if isinstance(attribution.get("recovery_focus"), list)
                    else []
                )
                if recovery_focus:
                    text = str(recovery_focus[0]).strip()
                    if text:
                        lines.append(f"  실패 귀속 {text}")
                loss_cooldown = (
                    validation_gate.get("loss_cooldown")
                    if isinstance(validation_gate.get("loss_cooldown"), dict)
                    else {}
                )
                cooldown_symbols = (
                    loss_cooldown.get("symbols")
                    if isinstance(loss_cooldown.get("symbols"), list)
                    else []
                )
                cooldown_groups = (
                    loss_cooldown.get("groups")
                    if isinstance(loss_cooldown.get("groups"), list)
                    else []
                )
                cooldown_lines: list[str] = []
                for item in cooldown_symbols[:2]:
                    if not isinstance(item, dict):
                        continue
                    symbol = str(item.get("symbol") or "").strip()
                    if not symbol:
                        continue
                    parts = [
                        _loss_cooldown_action_label(item.get("action")),
                        f"net {float(item.get('total_net_pnl') or 0):.2f}"
                        if item.get("total_net_pnl") is not None
                        else "",
                        f"PF {float(item.get('profit_factor') or 0):.2f}"
                        if item.get("profit_factor") is not None
                        else "",
                    ]
                    detail = " · ".join(part for part in parts if part)
                    cooldown_lines.append(f"{symbol} {detail}".strip())
                for item in cooldown_groups[:2]:
                    if not isinstance(item, dict):
                        continue
                    group = str(item.get("group") or "").strip()
                    if not group:
                        continue
                    label = str(item.get("group_type") or "group").strip()
                    action = _loss_cooldown_action_label(item.get("action"))
                    cooldown_lines.append(f"{label}={group} {action}".strip())
                if cooldown_lines:
                    lines.append(f"  손실 쿨다운 {', '.join(cooldown_lines[:3])}")
                guidance = (
                    validation_gate.get("operator_guidance")
                    if isinstance(validation_gate.get("operator_guidance"), list)
                    else []
                )
                for item in guidance[:2]:
                    text = str(item).strip()
                    if text:
                        lines.append(f"  조치 {text}")
            repair_execution = (
                row.get("repair_execution")
                if isinstance(row.get("repair_execution"), dict)
                else {}
            )
            repair_actions = (
                repair_execution.get("actions")
                if isinstance(repair_execution.get("actions"), list)
                else []
            )
            if repair_execution:
                repair_status = _repair_execution_status_label(
                    repair_execution.get("status")
                )
                repair_parts = [
                    f"실행 {_fmt_num(repair_execution.get('executed_count'))}",
                    f"대기 {_fmt_num(repair_execution.get('queued_count'))}",
                    (
                        f"오류 {_fmt_num(repair_execution.get('error_count'))}"
                        if repair_execution.get("error_count") not in (None, "")
                        else ""
                    ),
                    str(repair_execution.get("m1_execution_posture") or "").strip(),
                ]
                lines.append(
                    f"  복구 실행 {repair_status} · "
                    f"{' · '.join(part for part in repair_parts if part)}"
                )
                for action in repair_actions[:3]:
                    if not isinstance(action, dict):
                        continue
                    discipline_id = str(action.get("discipline_id") or "-").strip()
                    action_status = _repair_execution_status_label(action.get("status"))
                    mode = str(
                        action.get("validation_mode")
                        or action.get("artifact")
                        or ""
                    ).strip()
                    flags = [
                        "scale-up 차단" if action.get("scale_up_blocked") else "",
                        "live shadow 필요" if action.get("live_shadow_required") else "",
                    ]
                    detail = " · ".join(
                        part for part in [action_status, mode, *flags] if part
                    )
                    lines.append(f"  복구 {discipline_id}: {detail}")
            for card in scorecards[:3]:
                lines.append(
                    "  "
                    f"{card.get('strategy_family') or '-'} / {card.get('evidence_key') or 'all'} "
                    f"· {card.get('grade') or '-'} "
                    f"· n={card.get('sample_count') or 0} "
                    f"· win={_fmt_num(card.get('win_rate'))}%"
                )
        return self._html_pre("\n".join(lines))

    def llm_usage_text(self, payload: dict[str, Any]) -> str:
        total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
        rows = payload.get("by_component") if isinstance(payload.get("by_component"), list) else []
        lines = [
            f"LLM 사용량 · {payload.get('trading_day') or '-'}",
            f"- 호출 {_fmt_num(total.get('call_count'))}회",
            f"- 총 토큰 {_fmt_num(total.get('total_tokens'))}",
            f"- 입력/출력 {_fmt_num(total.get('prompt_tokens'))} / {_fmt_num(total.get('completion_tokens'))}",
            f"- 추정 집계 {_fmt_num(total.get('estimated_token_count'))}건 · 실패 {_fmt_num(total.get('error_count'))}건",
        ]
        for row in rows[:8]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('component') or '-'}: "
                f"{_fmt_num(row.get('call_count'))}회 · {_fmt_num(row.get('total_tokens'))} tokens"
            )
        return self._html_pre("\n".join(lines))

    def strategy_query_text(self, command: str, args: list[str]) -> str:
        text = " ".join(str(item) for item in args).strip()
        if text:
            return text[:600]
        if command == "strategy":
            return "다음 거래일 관심 후보를 전략적으로 정리해줘"
        return "현재 시장과 내 전략 기준으로 볼 후보와 피해야 할 조건을 알려줘"

    def strategy_brief_text(self, payload: dict[str, Any]) -> str:
        candidates = list(payload.get("candidates") or [])
        sources = list(payload.get("sources") or [])
        regime = payload.get("regime") if isinstance(payload.get("regime"), dict) else {}
        lines = [
            "HERMES 전략 인텔리전스",
            f"질문: {payload.get('query') or '-'}",
            f"모델: {payload.get('model') or '-'} · 모드: {payload.get('brief_mode') or '-'}",
            f"시장: {regime.get('label') or '-'} · {regime.get('stance') or '-'}",
            "",
        ]
        brief = str(payload.get("brief_md") or "").strip()
        if brief:
            lines.extend([brief[:1800], ""])
        if candidates:
            lines.append("상위 후보")
            for row in candidates[:5]:
                lines.append(
                    "- "
                    f"{_symbol_label(row)}: "
                    f"{_strategy_suitability_line(row)}"
                )
        if sources:
            lines.extend(["", "소스 상태"])
            for row in sources[:5]:
                lines.append(
                    f"- {row.get('label') or row.get('source_id')}: "
                    f"{row.get('status')} · {row.get('count', 0)}"
                )
        lines.extend(
            [
                "",
                TRADING_FOOTER,
            ]
        )
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def strategy_watchlist_text(self, payload: dict[str, Any]) -> str:
        candidates = list(payload.get("candidates") or [])
        regime = payload.get("regime") if isinstance(payload.get("regime"), dict) else {}
        lines = [
            "HERMES 전략 Watchlist",
            f"기준: {payload.get('query') or '-'}",
            f"시장: {regime.get('label') or '-'} · {regime.get('stance') or '-'}",
            f"모델: {payload.get('model') or '-'} · 모드: {payload.get('brief_mode') or '-'}",
            "",
        ]
        if not candidates:
            lines.append("현재 근거 기준으로 관심 후보가 부족합니다.")
        for idx, row in enumerate(candidates[:8], start=1):
            reason = "; ".join(list(row.get("reasons") or [])[:2]) or "근거 보강 필요"
            check = "; ".join(list(row.get("checks") or [])[:1]) or "진입 조건 확인 필요"
            warning = _strategy_warning_line(row)
            lines.extend(
                [
                    f"{idx}. {_symbol_label(row)}",
                    f"   {_strategy_suitability_line(row)}",
                    *([f"   자료: {warning}"] if warning else []),
                    f"   근거: {reason}",
                    f"   체크: {check}",
                ]
            )
        lines.extend(["", "상세는 /why <종목코드> 로 확인하세요.", TRADING_FOOTER])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def strategy_why_text(self, payload: dict[str, Any], symbol: str) -> str:
        target = str(symbol or "").strip()
        candidates = list(payload.get("candidates") or [])
        row = next(
            (
                item
                for item in candidates
                if str(item.get("symbol") or "").strip() == target
            ),
            None,
        )
        if not row:
            known = ", ".join(
                str(item.get("symbol") or "")
                for item in candidates[:8]
                if str(item.get("symbol") or "").strip()
            )
            return self._html_pre(
                f"{target or '-'} 후보 상세를 찾지 못했습니다.\n"
                f"현재 watchlist: {known or '없음'}"
            )

        components = row.get("score_components") or {}
        short = _strategy_horizon(row, "short_term")
        mid = _strategy_horizon(row, "mid_term")
        long = _strategy_horizon(row, "long_term")
        balanced = _strategy_horizon(row, "balanced")
        reasons = list(row.get("reasons") or [])
        checks = list(row.get("checks") or [])
        risks = list(row.get("risks") or [])
        facts = list(row.get("facts") or [])
        citations = list(row.get("citations") or [])
        report_ids = list(row.get("report_ids") or [])
        warning = _strategy_warning_line(row)
        lines = [
            "HERMES 후보 상세",
            _symbol_label(row, fallback_symbol=target),
            (
                f"균형 {balanced['grade']} {balanced['score']} · confidence {row.get('confidence')} · "
                f"stance {row.get('stance')}"
            ),
            (
                f"기간별: 단기 {short['grade']} {short['score']} / "
                f"중기 {mid['grade']} {mid['score']} / 장기 {long['grade']} {long['score']}"
            ),
            (
                "점수: "
                f"report {components.get('report', '-')} / research {components.get('research', '-')} / "
                f"whale {components.get('whale', '-')} / close {components.get('after_close', '-')} / "
                f"value {components.get('valuation', '-')} / risk {components.get('risk_penalty', '-')}"
            ),
        ]
        if warning:
            lines.append(f"자료 상태: {warning}")
        lines.extend(
            [
                "",
                "기간별 핵심 근거",
                f"- 단기: {'; '.join(short['drivers'][:2]) or '근거 보강 필요'}",
                f"- 중기: {'; '.join(mid['drivers'][:2]) or '근거 보강 필요'}",
                f"- 장기: {'; '.join(long['drivers'][:2]) or '근거 보강 필요'}",
                "",
                "왜 보는가",
            ]
        )
        lines.extend(f"- {item}" for item in (reasons[:5] or ["근거 보강 필요"]))
        lines.append("")
        lines.append("확인 조건")
        lines.extend(f"- {item}" for item in (checks[:4] or ["가격/거래대금/섹터 수급 확인"]))
        lines.append("")
        lines.append("반론")
        lines.extend(f"- {item}" for item in (risks[:4] or ["리스크 추가 점검"]))
        if facts:
            lines.extend(["", "근거 문장"])
            lines.extend(f"- {item}" for item in facts[:4])
        if report_ids:
            lines.extend(["", f"리포트: {', '.join(str(item) for item in report_ids[:6])}"])
        if citations:
            lines.extend(["근거 위치"])
            lines.extend(f"- {item}" for item in citations[:4])
        lines.extend(["", TRADING_FOOTER])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def _strategy_usage_text(self, args: list[str]) -> str:
        if args:
            return self._html_pre(
                "전략 인텔리전스 질문은 서버에서 처리됩니다.\n"
                "텔레그램 웹훅/폴링 경로로 /ask 또는 /strategy 명령을 보내주세요."
            )
        return self._html_pre(
            "사용법:\n"
            "/ask 다음 거래일 관심 후보를 전략적으로 정리해줘\n"
            "/strategy 고래 포지션과 종가 수급이 겹치는 후보를 봐줘\n"
            "/bot 오늘 시장에서 피해야 할 조건은?\n"
            "/watchlist\n"
            "/why 005930\n"
            "/market\n"
            "/judge\n"
            "/why-now 005930\n"
            "/memory\n"
            "/mindset\n"
            "/journal\n"
            "/reflect\n"
            "/weekly-review\n"
            "/monthly-review\n"
            "/policy\n"
            "/why-block blk_005930_..."
        )

    def _memory_usage_text(self, args: list[str]) -> str:
        _ = args
        return self._html_pre(
            "메모리 명령은 서버에서 처리됩니다.\n"
            "/memory - 활성 메모리/운용 원칙\n"
            "/mindset - 오늘 장전 마음가짐\n"
            "/journal - 오늘 메모리 저널\n"
            "/reflect - 최근 블록 거래 반성 실행\n"
            "/weekly-review - 주간 운용 반성 실행\n"
            "/monthly-review - 월간 운용 반성 실행\n"
            "/policy - 최근 정책 개정안\n"
            "/why-block <block_id> - 블록별 기억/교훈"
        )

    def memory_status_text(self, payload: dict[str, Any]) -> str:
        policies = list(payload.get("active_policies") or [])
        journals = list(payload.get("today_journals") or [])
        latest = payload.get("latest_run") if isinstance(payload.get("latest_run"), dict) else {}
        lines = [
            "HERMES 메모리",
            f"상태: {payload.get('status') or '-'} · 모델 {payload.get('model') or '-'}",
            (
                f"저널 {int(payload.get('journal_count') or 0)}개 · "
                f"기억 {int(payload.get('insight_count') or 0)}개 · "
                f"활성 원칙 {int(payload.get('active_policy_count') or 0)}개"
            ),
            f"최근 실행: {latest.get('slot') or latest.get('status') or '-'} · {latest.get('run_at') or '-'}",
            "",
        ]
        horizon_allocation = (
            payload.get("horizon_allocation")
            if isinstance(payload.get("horizon_allocation"), dict)
            else {}
        )
        horizon_items = [
            row
            for row in list(horizon_allocation.get("items") or [])
            if isinstance(row, dict)
        ]
        if horizon_items:
            lines.append("Horizon 밸런스")
            lines.extend(
                [
                    (
                        f"- {_horizon_label(row.get('horizon'))} "
                        f"{_fmt_weight_pct(row.get('current_weight'))}"
                        f" / 목표 {_fmt_weight_pct(row.get('target_weight'))}"
                    )
                    for row in horizon_items[:6]
                ]
            )
            lines.append("")
        lines.append("오늘 저널")
        if journals:
            for row in journals[:4]:
                lines.append(f"- {row.get('slot_label') or row.get('slot')}: {row.get('title') or '-'}")
        else:
            lines.append("- 아직 오늘 저널이 없습니다.")
        lines.append("")
        lines.append("활성 운용 원칙")
        if policies:
            for row in policies[:6]:
                lines.append(
                    f"- {row.get('policy_id')}: {row.get('reason') or row.get('action')}"
                )
        else:
            lines.append("- 아직 활성화된 메모리 정책이 없습니다.")
        lines.extend(["", TRADING_FOOTER])
        return self._html_pre("\n".join(lines)[:3600])

    def memory_journal_text(self, journal: dict[str, Any]) -> str:
        if not journal:
            return self._html_pre("아직 표시할 메모리 저널이 없습니다.")
        message = str(journal.get("message_md") or "").strip()
        title = str(journal.get("title") or journal.get("slot_label") or "메모리 저널")
        lines = [
            f"HERMES {title}",
            f"날짜: {journal.get('trading_day') or '-'} · 슬롯: {journal.get('slot') or '-'}",
            "",
            message or "내용이 비어 있습니다.",
        ]
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def memory_today_text(self, payload: dict[str, Any]) -> str:
        journals = list(payload.get("journals") or [])
        policies = list(payload.get("active_policies") or [])
        lines = [
            "HERMES 오늘의 메모리 저널",
            f"날짜: {payload.get('trading_day') or '-'}",
            "",
        ]
        if not journals:
            lines.append("아직 오늘 생성된 저널이 없습니다. /mindset 으로 장전 마음가짐을 만들 수 있습니다.")
        for row in journals:
            message = str(row.get("message_md") or "").strip()
            lines.extend(
                [
                    f"[{row.get('slot_label') or row.get('slot')}] {row.get('title') or '-'}",
                    _truncate_display(message, 420),
                    "",
                ]
            )
        if policies:
            lines.append("활성 운용 원칙")
            for row in policies[:5]:
                lines.append(f"- {row.get('policy_id')}: {row.get('reason') or row.get('action')}")
        lines.extend(["", TRADING_FOOTER])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def memory_block_text(self, payload: dict[str, Any]) -> str:
        block_id = str(payload.get("block_id") or "-")
        if payload.get("status") != "ok":
            return self._html_pre(f"{block_id} 블록 메모리를 찾을 수 없습니다.")
        content = str(payload.get("content") or "").strip()
        insights = list(payload.get("insights") or [])
        lines = [
            "HERMES 블록 기억",
            f"블록: {block_id}",
            f"파일: {'있음' if payload.get('exists') else '없음'}",
            "",
        ]
        if content:
            lines.append(_truncate_display(content, 1800))
        elif insights:
            lines.extend(f"- {row.get('summary_md')}" for row in insights[:5])
        else:
            lines.append("아직 이 블록에 쌓인 반성 메모리가 없습니다.")
        lines.extend(["", TRADING_FOOTER])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def memory_period_review_text(self, payload: dict[str, Any]) -> str:
        period_type = str(payload.get("period_type") or "-")
        review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
        review_md = str(review.get("review_md") or "").strip()
        lines = [
            f"쥬 {period_type} 반성",
            str(review.get("period_key") or payload.get("period_key") or "-"),
            f"정책 개정안 {int(payload.get('revision_count') or 0)}개",
            "",
            review_md or "아직 표시할 반성 내용이 없습니다.",
            "",
            TRADING_FOOTER,
        ]
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def memory_policy_revisions_text(self, payload: dict[str, Any]) -> str:
        rows = payload.get("items") if isinstance(payload.get("items"), list) else []
        lines = ["쥬 정책 개정안"]
        if rows:
            for row in rows[:8]:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    f"- {row.get('policy_id') or '-'} · "
                    f"{row.get('status') or '-'} · {row.get('scope') or 'general'}"
                )
        else:
            lines.append("- 최근 정책 개정안이 없습니다.")
        lines.extend(["", TRADING_FOOTER])
        return self._html_pre("\n".join(lines)[:3600])

    def market_judgment_text(self, payload: dict[str, Any]) -> str:
        run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        judgments = list(payload.get("judgments") or [])
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        clock = payload.get("clock") if isinstance(payload.get("clock"), dict) else {}
        source_snapshot = run.get("source_snapshot") if isinstance(run.get("source_snapshot"), dict) else {}
        if not clock and isinstance(source_snapshot.get("clock"), dict):
            clock = source_snapshot["clock"]
        lines = [
            "HERMES 장중 판단",
            f"시장: {clock.get('session') or run.get('market_session') or '-'} · {clock.get('now') or run.get('run_at') or '-'}",
            f"모드: {run.get('mode') or '-'} · 상태: {payload.get('status') or run.get('status') or '-'}",
        ]
        if account:
            lines.append(
                f"국장1: 현금 {_fmt_krw_won(account.get('cash_krw'))} · "
                f"보유 {_fmt_krw_won(account.get('position_value_krw'))} · "
                f"{int(account.get('position_count') or 0)}종목"
            )
        lines.append("")
        if not judgments:
            lines.append("아직 저장된 장중 판단이 없습니다.")
        for row in judgments[:8]:
            reasons = "; ".join(list(row.get("reasons") or [])[:2]) or "근거 보강 필요"
            risks = "; ".join(list(row.get("risks") or [])[:1]) or "리스크 점검"
            quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
            price = quote.get("price")
            change_pct = quote.get("change_pct")
            price_line = ""
            if price:
                price_line = f" · {int(float(price)):,.0f}원 / {float(change_pct or 0):+.2f}%"
            lines.extend(
                [
                    f"- {_symbol_label(row)}{price_line}",
                    (
                        f"  {_market_action_label(row.get('account_action'))} · "
                        f"{row.get('stance') or '-'} · confidence {float(row.get('confidence') or 0):.2f}"
                    ),
                    f"  근거: {reasons}",
                    f"  반론: {risks}",
                ]
            )
        lines.extend(["", TRADING_FOOTER])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def market_why_now_text(self, payload: dict[str, Any], symbol: str) -> str:
        target = str(symbol or "").strip()
        judgments = list(payload.get("judgments") or [])
        row = next(
            (
                item
                for item in judgments
                if str(item.get("symbol") or "").strip() == target
            ),
            None,
        )
        if not row:
            known = ", ".join(
                str(item.get("symbol") or "")
                for item in judgments[:10]
                if str(item.get("symbol") or "").strip()
            )
            return self._html_pre(
                f"{target or '-'} 장중 판단을 찾지 못했습니다.\n"
                f"현재 판단 종목: {known or '없음'}"
            )
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        position = row.get("position") if isinstance(row.get("position"), dict) else {}
        strategy = row.get("strategy") if isinstance(row.get("strategy"), dict) else {}
        lines = [
            "HERMES 왜 지금?",
            _symbol_label(row, fallback_symbol=target),
            f"판단: {_market_action_label(row.get('account_action'))} · {row.get('stance')} · {row.get('horizon')}",
            f"confidence: {float(row.get('confidence') or 0):.2f}",
            "",
            "현재 시세",
            f"- 가격: {_fmt_krw_won(quote.get('price'))} · 등락률 {float(quote.get('change_pct') or 0):+.2f}%",
            f"- 소스: {quote.get('source') or '-'} · 상태 {quote.get('status') or '-'}",
        ]
        if position:
            lines.extend(
                [
                    "",
                    "국장1 보유",
                    f"- 평가금액: {_fmt_krw_won(position.get('value_krw'))}",
                    f"- 손익: {_fmt_signed_krw_won(position.get('unrealized_pnl_krw'))} / {float(position.get('unrealized_pnl_pct') or 0):+.2f}%",
                    f"- 비중: {float(position.get('position_weight') or 0) * 100:.1f}%",
                ]
            )
        if strategy:
            lines.extend(
                [
                    "",
                    "전략 레이어",
                    f"- 균형 적합도: {strategy.get('score', '-')}",
                    f"- confidence: {strategy.get('confidence', '-')}",
                ]
            )
        lines.extend(["", "근거"])
        lines.extend(f"- {item}" for item in (list(row.get("reasons") or [])[:5] or ["근거 보강 필요"]))
        lines.append("")
        lines.append("반론")
        lines.extend(f"- {item}" for item in (list(row.get("risks") or [])[:4] or ["리스크 추가 점검"]))
        gaps = list(row.get("data_gaps") or [])
        if gaps:
            lines.extend(["", "자료 공백"])
            lines.extend(f"- {item}" for item in gaps[:4])
        lines.extend(["", TRADING_FOOTER])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

    def _market_usage_text(self, args: list[str]) -> str:
        _ = args
        return self._html_pre(
            "장중 판단 명령은 서버에서 처리됩니다.\n"
            "/market - 최신 장중 판단 요약\n"
            "/judge - 새 장중 판단 실행\n"
            "/why-now 005930 - 현재 시세/계좌 기준 상세"
        )

    def _status_text(self, dashboard: dict[str, Any]) -> str:
        lines = [
            "HERMES BOT 상태",
            f"기준시각(KST): {_fmt_kst(str(dashboard.get('clock_utc') or ''))}",
            f"전체 자산: {_fmt_krw(dashboard.get('portfolio_total_krw'))}",
            f"현금 자산: {_fmt_krw(dashboard.get('cash_total_krw'))}",
            f"투자 자산: {_fmt_krw(dashboard.get('invested_total_krw'))}",
            f"평가 손익: {_fmt_signed_krw(dashboard.get('unrealized_pnl_krw'))}",
            f"연동 시장 수: {int(dashboard.get('venue_count') or 0)}",
        ]

        fx = dashboard.get("fx")
        if isinstance(fx, dict):
            usdt_source = str(fx.get("usdt_source") or "-")
            usd_source = str(fx.get("usd_source") or "-")
            fx_status_raw = str(fx.get("status") or "").lower()
            fx_status = "주의" if fx_status_raw == "warn" else "정상"
            lines.extend(
                [
                    f"USDT/KRW: {_fmt_rate(fx.get('usdt_krw'))} ({usdt_source})",
                    f"USD/KRW: {_fmt_rate(fx.get('usd_krw'))} ({usd_source})",
                    f"FX 상태: {fx_status} · 갱신 {_fmt_kst(str(fx.get('fetched_at') or ''))}",
                ]
            )

        text = "\n".join(lines)
        return self._html_pre(text)

    def _venues_text(self, dashboard: dict[str, Any], args: list[str]) -> str:
        venues = list(dashboard.get("venues", []))
        if not args:
            headers = ["ID", "TOT", "CASH", "PNL"]
            rows = [
                [
                    str(v.get("id") or "-"),
                    self._fmt_krw_compact(v.get("total_krw")),
                    self._fmt_krw_compact(v.get("cash_krw")),
                    self._fmt_signed_krw_compact(v.get("unrealized_pnl_krw")),
                ]
                for v in venues
            ]
            guide = "상세: /balance <id> (예: /balance upbit)"
            body = "\n".join(
                [
                    f"기준시각(KST): {_fmt_kst(str(dashboard.get('clock_utc') or ''))}",
                    "",
                    self._render_table(headers, rows),
                    "",
                    guide,
                ]
            )
            return self._html_pre(body)

        target = self._resolve_venue(args[0], venues)
        if not target:
            known = ", ".join(str(v.get("id")) for v in venues)
            return self._html_pre(f"알 수 없는 거래소: {args[0]}\n가능 값: {known}")

        assets = list(target.get("assets", []))
        headers = ["QTY", "VAL", "PNL", "NAME"]
        rows = [
            [
                self._fmt_qty(a.get("qty")),
                self._fmt_krw_compact(a.get("value_krw")),
                self._fmt_signed_krw_compact(a.get("pnl_krw")),
                self._asset_label(a),
            ]
            for a in assets
        ]
        detail = "\n".join(
            [
                f"{target.get('label')} ({target.get('id')})",
                f"총자산: {_fmt_krw_won(target.get('total_krw'))}",
                f"현금: {_fmt_krw_won(target.get('cash_krw'))}",
                f"투자: {_fmt_krw_won(target.get('invested_krw'))}",
                f"손익: {_fmt_signed_krw_won(target.get('unrealized_pnl_krw'))}",
                "",
                self._render_table(headers, rows),
            ]
        )
        return self._html_pre(detail)

    def _sessions_text(self, dashboard: dict[str, Any]) -> str:
        lines = [
            "봇 세션 요약",
            f"기준시각(KST): {_fmt_kst(str(dashboard.get('clock_utc') or ''))}",
            "",
        ]
        for session in dashboard.get("sessions", []):
            mode = str(session.get("mode") or "")
            venue = str(session.get("venue_label") or session.get("venue_id") or "")
            sid = str(session.get("session_id") or "-")
            status = str(session.get("status") or "-")
            if mode == "short_term":
                net = (
                    float(session.get("realized_pnl_krw") or 0)
                    + float(session.get("unrealized_pnl_krw") or 0)
                    + float(session.get("fees_paid_krw") or 0)
                )
                symbol = str(session.get("trade_symbol") or "-")
                lines.append(
                    f"- {sid} | {venue} | 단타 | {status} | {symbol} | 순손익 {_fmt_signed_krw(net)}"
                )
            else:
                alpha = float(session.get("portfolio_return_30d_pct") or 0) - float(
                    session.get("benchmark_return_30d_pct") or 0
                )
                drift = _fmt_pct(session.get("allocation_drift_pct"))
                due = str(session.get("rebalance_due") or "-")
                lines.append(
                    f"- {sid} | {venue} | 밸런스 | {status} | 알파 {alpha:+.2f}% | 드리프트 {drift} | 리밸런스 {due}"
                )
        return self._html_pre("\n".join(lines))

    def _session_detail_text(self, dashboard: dict[str, Any], args: list[str]) -> str:
        sessions: list[dict[str, Any]] = list(dashboard.get("sessions", []))
        if not args:
            available = ", ".join(str(s.get("session_id")) for s in sessions[:8]) or "-"
            return self._html_pre(
                "사용법: /session <session_id>\n"
                f"예시 ID: {available}"
            )

        target_id = args[0].strip().lower()
        selected = None
        for session in sessions:
            sid = str(session.get("session_id") or "").lower()
            if sid == target_id:
                selected = session
                break

        if not selected:
            return self._html_pre(f"세션을 찾을 수 없습니다: {args[0]}")

        sid = str(selected.get("session_id") or "-")
        venue = str(selected.get("venue_label") or selected.get("venue_id") or "-")
        mode = str(selected.get("mode") or "-")
        status = str(selected.get("status") or "-")
        cycle = int(selected.get("cycle_sec") or 0)
        lines = [
            f"세션 상세: {sid}",
            f"거래소: {venue}",
            f"모드: {mode}",
            f"상태: {status}",
            f"사이클: {cycle}s",
        ]

        if mode == "short_term":
            net = (
                float(selected.get("realized_pnl_krw") or 0)
                + float(selected.get("unrealized_pnl_krw") or 0)
                + float(selected.get("fees_paid_krw") or 0)
            )
            lines.extend(
                [
                    f"종목: {selected.get('trade_symbol') or '-'}",
                    f"포지션: {selected.get('position_side') or '-'}",
                    f"실시간 순손익: {_fmt_signed_krw(net)}",
                    f"손절/익절: {_fmt_krw(selected.get('stop_loss_price'))} / {_fmt_krw(selected.get('take_profit_price'))}",
                ]
            )
            return self._html_pre("\n".join(lines))

        alpha = float(selected.get("portfolio_return_30d_pct") or 0) - float(
            selected.get("benchmark_return_30d_pct") or 0
        )
        lines.extend(
            [
                f"30일 수익률: {_fmt_pct(selected.get('portfolio_return_30d_pct'))}",
                f"벤치마크: {_fmt_pct(selected.get('benchmark_return_30d_pct'))}",
                f"알파: {alpha:+.2f}%",
                f"드리프트: {_fmt_pct(selected.get('allocation_drift_pct'))}",
                "종목 목표:",
            ]
        )
        for target in selected.get("targets", [])[:10]:
            symbol = str(target.get("symbol") or "-")
            tw = _fmt_pct(target.get("target_weight_pct"), 1)
            cw = _fmt_pct(target.get("current_weight_pct"), 1)
            stop = _fmt_krw(target.get("stop_loss_price"))
            take = _fmt_krw(target.get("take_profit_price"))
            lines.append(f"- {symbol} | 목표/현재 {tw}/{cw} | 손절 {stop} | 익절 {take}")
        return self._html_pre("\n".join(lines))

    def _render_table(self, headers: list[str], rows: list[list[str]]) -> str:
        if not rows:
            return "(empty)"

        max_col_width = 11
        widths = [_display_width(str(col)) for col in headers]
        for row in rows:
            for idx, col in enumerate(row):
                widths[idx] = max(widths[idx], _display_width(str(col)))
        widths = [min(max_col_width, width) for width in widths]

        def fmt(values: list[str]) -> str:
            cols = []
            for idx, value in enumerate(values):
                raw = _truncate_display(str(value), widths[idx])
                pad = max(widths[idx] - _display_width(raw), 0)
                cols.append(raw + (" " * pad))
            return "|".join(cols)

        header_line = fmt([str(col) for col in headers])
        divider_line = "|".join("-" * width for width in widths)
        body_lines = [fmt([str(col) for col in row]) for row in rows]
        return "\n".join([header_line, divider_line, *body_lines])

    def _resolve_venue(self, token: str, venues: list[dict[str, Any]]) -> dict[str, Any] | None:
        normalized = token.strip().lower()
        alias = {
            "upbit": "upbit",
            "bithumb": "bithumb",
            "bth": "bithumb",
            "binance": "binance",
            "binance_spot": "binance",
            "binancef": "binance_futures",
            "binf": "binance_futures",
            "futures": "binance_futures",
            "binance_futures": "binance_futures",
            "krx": "kr_stock",
            "kr": "kr_stock",
            "korea": "kr_stock",
            "kr2": "kr_stock_2",
            "wife": "kr_stock_2",
            "kis2": "kr_stock_2",
            "us": "us_stock",
            "usa": "us_stock",
            "nyse": "us_stock",
            "nasdaq": "us_stock",
        }.get(normalized, normalized)

        for venue in venues:
            vid = str(venue.get("id") or "").lower()
            vlabel = str(venue.get("label") or "").lower()
            if alias == vid or normalized == vlabel:
                return venue
        return None

    def _fmt_qty(self, value: Any) -> str:
        try:
            qty = float(value)
        except (TypeError, ValueError):
            return "-"
        abs_qty = abs(qty)
        if abs_qty >= 1000:
            return _compact_number(qty, digits=2)
        if abs_qty >= 1:
            return f"{qty:.2f}".rstrip("0").rstrip(".")
        if abs_qty >= 0.01:
            return f"{qty:.4f}".rstrip("0").rstrip(".")
        if abs_qty >= 0.0001:
            return f"{qty:.6f}".rstrip("0").rstrip(".")
        if abs_qty > 0:
            return f"{qty:.2e}"
        return "0"

    def _html_pre(self, text: str) -> str:
        return f"<pre>{html.escape(text)}</pre>"

    def _asset_label(self, asset: dict[str, Any]) -> str:
        symbol = str(asset.get("asset") or "-")
        symbol_name = str(asset.get("asset_name") or "").strip() or symbol
        if str(asset.get("kind") or "") == "cash":
            return f"{symbol}*"
        return symbol_name

    def _fmt_krw_compact(self, value: float | int | None) -> str:
        return f"{_compact_number(value)}"

    def _fmt_signed_krw_compact(self, value: float | int | None) -> str:
        amount = float(value or 0)
        sign = "+" if amount >= 0 else "-"
        return f"{sign}{_compact_number(abs(amount))}"
