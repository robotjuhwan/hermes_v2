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
            ]
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
