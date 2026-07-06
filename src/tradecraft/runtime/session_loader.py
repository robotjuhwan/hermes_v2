from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tradecraft.runtime.state_store import utc_now_iso


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list_str(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        out = [str(item).strip() for item in value if str(item).strip()]
        if out:
            return out
    return list(default)


def _default_sessions(now_iso: str) -> list[dict[str, Any]]:
    return [
        {
            "session_id": "upbit_scalper",
            "venue_id": "upbit",
            "venue_label": "업비트",
            "name": "단기 세션",
            "bot_name": "upbit_short_core",
            "mode": "short_term",
            "strategy_id": "noop_short_term",
            "status": "RUNNING",
            "cycle_sec": 20,
            "active_markets": ["UPBIT"],
            "strategy_count": 1,
            "trade_count_today": 0,
            "realized_pnl_krw": 0.0,
            "unrealized_pnl_krw": 0.0,
            "fees_paid_krw": 0.0,
            "volume_traded_krw": 0.0,
            "trade_symbol": "BTC/KRW",
            "position_side": "FLAT",
            "entry_price": 0.0,
            "stop_loss_price": 0.0,
            "take_profit_price": 0.0,
            "max_notional_krw": 3_000_000.0,
            "holding_limit_min": 45,
            "win_rate_pct": 0.0,
            "avg_holding_min": 0.0,
            "intraday_drawdown_pct": 0.0,
            "fee_breakdown": {"maker_krw": 0.0, "taker_krw": 0.0},
            "display_note": "전략 모듈 연결 전 skeleton 상태",
            "risk_guard": "ON",
            "last_heartbeat": now_iso,
        },
        {
            "session_id": "upbit_balance",
            "venue_id": "upbit",
            "venue_label": "업비트",
            "name": "밸런스 세션",
            "bot_name": "upbit_balance_core",
            "mode": "mid_long_term",
            "strategy_id": "noop_balance",
            "status": "RUNNING",
            "cycle_sec": 1800,
            "active_markets": ["UPBIT"],
            "strategy_count": 1,
            "trade_count_today": 0,
            "portfolio_return_30d_pct": 0.0,
            "benchmark_return_30d_pct": 0.0,
            "tracking_error_30d_pct": 0.0,
            "max_drawdown_1y_pct": 0.0,
            "turnover_30d_pct": 0.0,
            "fee_drag_30d_pct": 0.0,
            "allocation_drift_pct": 0.0,
            "cash_buffer_pct": 10.0,
            "rebalance_due": "대기",
            "rebalance_amount_krw": 0.0,
            "rebalance_lines": 0,
            "targets": [
                {
                    "symbol": "BTC",
                    "target_weight_pct": 60.0,
                    "current_weight_pct": 60.0,
                    "target_price": 0.0,
                    "stop_loss_price": 0.0,
                    "take_profit_price": 0.0,
                },
                {
                    "symbol": "ETH",
                    "target_weight_pct": 40.0,
                    "current_weight_pct": 40.0,
                    "target_price": 0.0,
                    "stop_loss_price": 0.0,
                    "take_profit_price": 0.0,
                },
            ],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "-", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "-", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "-", "status": "OK"},
            ],
            "display_note": "목표 비중 기반 리밸런스 골격",
            "risk_guard": "ON",
            "last_heartbeat": now_iso,
        },
        {
            "session_id": "binance_scalper",
            "venue_id": "binance",
            "venue_label": "바이낸스 현물",
            "name": "단기 세션",
            "bot_name": "binance_short_core",
            "mode": "short_term",
            "strategy_id": "noop_short_term",
            "status": "RUNNING",
            "cycle_sec": 20,
            "active_markets": ["BINANCE_SPOT"],
            "strategy_count": 1,
            "trade_count_today": 0,
            "realized_pnl_krw": 0.0,
            "unrealized_pnl_krw": 0.0,
            "fees_paid_krw": 0.0,
            "volume_traded_krw": 0.0,
            "trade_symbol": "BTC/USDT",
            "position_side": "FLAT",
            "entry_price": 0.0,
            "stop_loss_price": 0.0,
            "take_profit_price": 0.0,
            "max_notional_krw": 3_000_000.0,
            "holding_limit_min": 45,
            "win_rate_pct": 0.0,
            "avg_holding_min": 0.0,
            "intraday_drawdown_pct": 0.0,
            "fee_breakdown": {"maker_krw": 0.0, "taker_krw": 0.0},
            "display_note": "거래소별 단기 세션 독립 운용",
            "risk_guard": "ON",
            "last_heartbeat": now_iso,
        },
        {
            "session_id": "binance_balance",
            "venue_id": "binance",
            "venue_label": "바이낸스 현물",
            "name": "밸런스 세션",
            "bot_name": "binance_balance_core",
            "mode": "mid_long_term",
            "strategy_id": "noop_balance",
            "status": "RUNNING",
            "cycle_sec": 1800,
            "active_markets": ["BINANCE_SPOT"],
            "strategy_count": 1,
            "trade_count_today": 0,
            "portfolio_return_30d_pct": 0.0,
            "benchmark_return_30d_pct": 0.0,
            "tracking_error_30d_pct": 0.0,
            "max_drawdown_1y_pct": 0.0,
            "turnover_30d_pct": 0.0,
            "fee_drag_30d_pct": 0.0,
            "allocation_drift_pct": 0.0,
            "cash_buffer_pct": 10.0,
            "rebalance_due": "대기",
            "rebalance_amount_krw": 0.0,
            "rebalance_lines": 0,
            "targets": [
                {
                    "symbol": "BTC",
                    "target_weight_pct": 50.0,
                    "current_weight_pct": 50.0,
                    "target_price": 0.0,
                    "stop_loss_price": 0.0,
                    "take_profit_price": 0.0,
                },
                {
                    "symbol": "ETH",
                    "target_weight_pct": 30.0,
                    "current_weight_pct": 30.0,
                    "target_price": 0.0,
                    "stop_loss_price": 0.0,
                    "take_profit_price": 0.0,
                },
                {
                    "symbol": "SOL",
                    "target_weight_pct": 20.0,
                    "current_weight_pct": 20.0,
                    "target_price": 0.0,
                    "stop_loss_price": 0.0,
                    "take_profit_price": 0.0,
                },
            ],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "-", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "-", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "-", "status": "OK"},
            ],
            "display_note": "벤치마크 대비 리스크 제한 리밸런스",
            "risk_guard": "ON",
            "last_heartbeat": now_iso,
        },
        {
            "session_id": "krx_balance",
            "venue_id": "kr_stock",
            "venue_label": "국장1",
            "name": "밸런스 세션",
            "bot_name": "krx_balance_core",
            "mode": "mid_long_term",
            "strategy_id": "noop_balance",
            "status": "RUNNING",
            "cycle_sec": 3600,
            "active_markets": ["KRX"],
            "strategy_count": 1,
            "trade_count_today": 0,
            "portfolio_return_30d_pct": 0.0,
            "benchmark_return_30d_pct": 0.0,
            "tracking_error_30d_pct": 0.0,
            "max_drawdown_1y_pct": 0.0,
            "turnover_30d_pct": 0.0,
            "fee_drag_30d_pct": 0.0,
            "allocation_drift_pct": 0.0,
            "cash_buffer_pct": 15.0,
            "rebalance_due": "대기",
            "rebalance_amount_krw": 0.0,
            "rebalance_lines": 0,
            "targets": [],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "-", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "-", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "-", "status": "OK"},
            ],
            "display_note": "국장 리밸런스 골격",
            "risk_guard": "ON",
            "last_heartbeat": now_iso,
        },
        {
            "session_id": "us_balance",
            "venue_id": "us_stock",
            "venue_label": "미장",
            "name": "밸런스 세션",
            "bot_name": "us_balance_core",
            "mode": "mid_long_term",
            "strategy_id": "noop_balance",
            "status": "RUNNING",
            "cycle_sec": 3600,
            "active_markets": ["NASDAQ", "NYSE"],
            "strategy_count": 1,
            "trade_count_today": 0,
            "portfolio_return_30d_pct": 0.0,
            "benchmark_return_30d_pct": 0.0,
            "tracking_error_30d_pct": 0.0,
            "max_drawdown_1y_pct": 0.0,
            "turnover_30d_pct": 0.0,
            "fee_drag_30d_pct": 0.0,
            "allocation_drift_pct": 0.0,
            "cash_buffer_pct": 10.0,
            "rebalance_due": "대기",
            "rebalance_amount_krw": 0.0,
            "rebalance_lines": 0,
            "targets": [],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "-", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "-", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "-", "status": "OK"},
            ],
            "display_note": "미장 리밸런스 골격",
            "risk_guard": "ON",
            "last_heartbeat": now_iso,
        },
    ]


def _normalize(row: dict[str, Any], now_iso: str) -> dict[str, Any]:
    mode = str(row.get("mode") or "mid_long_term").strip()
    if mode != "short_term":
        mode = "mid_long_term"

    venue_id = str(row.get("venue_id") or "unknown").strip() or "unknown"
    session_id = str(row.get("session_id") or f"{venue_id}_{mode}").strip() or f"{venue_id}_{mode}"
    enabled = bool(row.get("enabled", True))

    normalized: dict[str, Any] = {
        "session_id": session_id,
        "venue_id": venue_id,
        "venue_label": str(row.get("venue_label") or venue_id).strip() or venue_id,
        "name": str(row.get("name") or ("단기 세션" if mode == "short_term" else "밸런스 세션")).strip(),
        "bot_name": str(row.get("bot_name") or f"{session_id}_bot").strip(),
        "mode": mode,
        "strategy_id": str(
            row.get("strategy_id") or ("noop_short_term" if mode == "short_term" else "noop_balance")
        ).strip(),
        "status": "RUNNING" if enabled else "PAUSED",
        "cycle_sec": max(_as_int(row.get("cycle_sec"), 20 if mode == "short_term" else 1800), 1),
        "active_markets": _as_list_str(row.get("active_markets"), [venue_id.upper()]),
        "strategy_count": max(_as_int(row.get("strategy_count"), 1), 1),
        "trade_count_today": max(_as_int(row.get("trade_count_today"), 0), 0),
        "display_note": str(row.get("display_note") or "runtime session").strip(),
        "risk_guard": str(row.get("risk_guard") or "ON").strip() or "ON",
        "last_heartbeat": str(row.get("last_heartbeat") or now_iso),
    }

    if mode == "short_term":
        normalized.update(
            {
                "realized_pnl_krw": _as_float(row.get("realized_pnl_krw"), 0.0),
                "unrealized_pnl_krw": _as_float(row.get("unrealized_pnl_krw"), 0.0),
                "fees_paid_krw": _as_float(row.get("fees_paid_krw"), 0.0),
                "volume_traded_krw": _as_float(row.get("volume_traded_krw"), 0.0),
                "trade_symbol": str(row.get("trade_symbol") or "-").strip() or "-",
                "position_side": str(row.get("position_side") or "FLAT").strip() or "FLAT",
                "entry_price": _as_float(row.get("entry_price"), 0.0),
                "stop_loss_price": _as_float(row.get("stop_loss_price"), 0.0),
                "take_profit_price": _as_float(row.get("take_profit_price"), 0.0),
                "max_notional_krw": _as_float(row.get("max_notional_krw"), 0.0),
                "holding_limit_min": max(_as_int(row.get("holding_limit_min"), 0), 0),
                "win_rate_pct": _as_float(row.get("win_rate_pct"), 0.0),
                "avg_holding_min": _as_float(row.get("avg_holding_min"), 0.0),
                "intraday_drawdown_pct": _as_float(row.get("intraday_drawdown_pct"), 0.0),
                "fee_breakdown": {
                    "maker_krw": _as_float((row.get("fee_breakdown") or {}).get("maker_krw"), 0.0),
                    "taker_krw": _as_float((row.get("fee_breakdown") or {}).get("taker_krw"), 0.0),
                },
            }
        )
        return normalized

    normalized.update(
        {
            "portfolio_return_30d_pct": _as_float(row.get("portfolio_return_30d_pct"), 0.0),
            "benchmark_return_30d_pct": _as_float(row.get("benchmark_return_30d_pct"), 0.0),
            "tracking_error_30d_pct": _as_float(row.get("tracking_error_30d_pct"), 0.0),
            "max_drawdown_1y_pct": _as_float(row.get("max_drawdown_1y_pct"), 0.0),
            "turnover_30d_pct": _as_float(row.get("turnover_30d_pct"), 0.0),
            "fee_drag_30d_pct": _as_float(row.get("fee_drag_30d_pct"), 0.0),
            "allocation_drift_pct": _as_float(row.get("allocation_drift_pct"), 0.0),
            "cash_buffer_pct": _as_float(row.get("cash_buffer_pct"), 0.0),
            "rebalance_due": str(row.get("rebalance_due") or "대기").strip() or "대기",
            "rebalance_amount_krw": _as_float(row.get("rebalance_amount_krw"), 0.0),
            "rebalance_lines": max(_as_int(row.get("rebalance_lines"), 0), 0),
            "targets": list(row.get("targets") or []),
            "principles": list(row.get("principles") or []),
        }
    )
    return normalized


def _normalize_rows(rows: list[dict[str, Any]], now_iso: str) -> list[dict[str, Any]]:
    normalized = [_normalize(row, now_iso) for row in rows if isinstance(row, dict)]
    if normalized:
        return normalized
    return [_normalize(row, now_iso) for row in _default_sessions(now_iso)]


def _read_session_file(path: Path) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("sessions")
    else:
        return None

    if not isinstance(rows, list):
        return None

    return [row for row in rows if isinstance(row, dict)]


def load_runtime_sessions(session_path: str) -> tuple[list[dict[str, Any]], str]:
    now_iso = utc_now_iso()
    raw_path = (session_path or "").strip()

    if not raw_path:
        defaults = _default_sessions(now_iso)
        return _normalize_rows(defaults, now_iso), "safe_default_no_orders"

    path = Path(raw_path)
    if not path.exists():
        defaults = _default_sessions(now_iso)
        return _normalize_rows(defaults, now_iso), f"safe_default_no_orders (missing: {path})"

    rows = _read_session_file(path)
    if rows is None:
        defaults = _default_sessions(now_iso)
        return _normalize_rows(defaults, now_iso), f"safe_default_no_orders (invalid file: {path})"

    return _normalize_rows(rows, now_iso), f"file:{path}"
