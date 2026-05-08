from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


DashboardProvider = Callable[[], dict[str, Any]]


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
    def __init__(self, dashboard_provider: DashboardProvider) -> None:
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
                "/why-block <block_id> - 블록별 기억/교훈",
            ]
        )

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
                    f"{row.get('name') or row.get('symbol')}({row.get('symbol')}): "
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
                "정보 제공용이며 매매 추천이 아닙니다.",
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
                    f"{idx}. {row.get('name') or row.get('symbol')}({row.get('symbol')})",
                    f"   {_strategy_suitability_line(row)}",
                    *([f"   자료: {warning}"] if warning else []),
                    f"   근거: {reason}",
                    f"   체크: {check}",
                ]
            )
        lines.extend(["", "상세는 /why <종목코드> 로 확인하세요.", "정보 제공용이며 매매 추천이 아닙니다."])
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
            f"{row.get('name') or target}({target})",
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
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
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
            "오늘 저널",
        ]
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
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
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
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
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
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
        text = "\n".join(lines)
        if len(text) > 3600:
            text = text[:3580].rstrip() + "\n..."
        return self._html_pre(text)

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
                    f"- {row.get('name') or row.get('symbol')}({row.get('symbol')}){price_line}",
                    (
                        f"  {_market_action_label(row.get('account_action'))} · "
                        f"{row.get('stance') or '-'} · confidence {float(row.get('confidence') or 0):.2f}"
                    ),
                    f"  근거: {reasons}",
                    f"  반론: {risks}",
                ]
            )
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
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
            f"{row.get('name') or target}({target})",
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
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
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
