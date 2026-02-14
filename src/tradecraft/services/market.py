from __future__ import annotations

from datetime import datetime, timezone


def recalculate_venue_totals(venues: list[dict]) -> tuple[float, float, float, float]:
    portfolio_total_krw = 0.0
    cash_total_krw = 0.0
    invested_total_krw = 0.0
    unrealized_pnl_krw = 0.0

    for venue in venues:
        assets = list(venue.get("assets", []))
        cash = sum(float(a.get("value_krw", 0.0)) for a in assets if a.get("kind") == "cash")
        holdings = sum(float(a.get("value_krw", 0.0)) for a in assets if a.get("kind") != "cash")
        pnl = sum(float(a.get("pnl_krw", 0.0)) for a in assets if a.get("kind") != "cash")
        total = cash + holdings

        venue["cash_krw"] = cash
        venue["invested_krw"] = holdings
        venue["unrealized_pnl_krw"] = pnl
        venue["total_krw"] = total

        portfolio_total_krw += total
        cash_total_krw += cash
        invested_total_krw += holdings
        unrealized_pnl_krw += pnl

    return portfolio_total_krw, cash_total_krw, invested_total_krw, unrealized_pnl_krw


def recalculate_dashboard_totals(dashboard: dict) -> dict:
    venues = list(dashboard.get("venues", []))
    portfolio, cash, invested, pnl = recalculate_venue_totals(venues)
    dashboard["portfolio_total_krw"] = portfolio
    dashboard["cash_total_krw"] = cash
    dashboard["invested_total_krw"] = invested
    dashboard["unrealized_pnl_krw"] = pnl
    dashboard["venue_count"] = len(venues)
    return dashboard


def replace_venue_assets(dashboard: dict, venue_id: str, assets: list[dict]) -> bool:
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["assets"] = assets
        recalculate_dashboard_totals(dashboard)
        return True
    return False


def upsert_venue_assets(
    dashboard: dict,
    venue_id: str,
    label: str,
    market: str,
    assets: list[dict],
) -> None:
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["label"] = label
        venue["market"] = market
        venue["assets"] = assets
        recalculate_dashboard_totals(dashboard)
        return

    venues = dashboard.setdefault("venues", [])
    venues.append(
        {
            "id": venue_id,
            "label": label,
            "market": market,
            "assets": assets,
        }
    )
    recalculate_dashboard_totals(dashboard)


def mock_dashboard() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    venues = [
        {
            "id": "upbit",
            "label": "업비트",
            "market": "국내 가상자산",
            "assets": [
                {
                    "asset": "KRW",
                    "asset_name": "KRW",
                    "kind": "cash",
                    "qty": 2_515_000.0,
                    "available": 2_400_000.0,
                    "locked": 115_000.0,
                    "avg_price": 1.0,
                    "mark_price": 1.0,
                    "value_krw": 2_515_000.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "BTC",
                    "asset_name": "Bitcoin",
                    "kind": "position",
                    "qty": 0.15,
                    "available": 0.15,
                    "locked": 0.0,
                    "avg_price": 146_200_000.0,
                    "mark_price": 147_800_000.0,
                    "value_krw": 22_170_000.0,
                    "pnl_krw": 240_000.0,
                },
                {
                    "asset": "ETH",
                    "asset_name": "Ethereum",
                    "kind": "position",
                    "qty": 1.20,
                    "available": 1.20,
                    "locked": 0.0,
                    "avg_price": 5_320_000.0,
                    "mark_price": 5_175_000.0,
                    "value_krw": 6_210_000.0,
                    "pnl_krw": -174_000.0,
                },
            ],
        },
        {
            "id": "bithumb",
            "label": "빗썸",
            "market": "국내 가상자산",
            "assets": [
                {
                    "asset": "KRW",
                    "asset_name": "KRW",
                    "kind": "cash",
                    "qty": 1_120_000.0,
                    "available": 1_000_000.0,
                    "locked": 120_000.0,
                    "avg_price": 1.0,
                    "mark_price": 1.0,
                    "value_krw": 1_120_000.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "XRP",
                    "asset_name": "Ripple",
                    "kind": "position",
                    "qty": 1_800.0,
                    "available": 1_800.0,
                    "locked": 0.0,
                    "avg_price": 780.0,
                    "mark_price": 805.0,
                    "value_krw": 1_449_000.0,
                    "pnl_krw": 45_000.0,
                },
            ],
        },
        {
            "id": "binance",
            "label": "바이낸스 현물",
            "market": "해외 가상자산 (Spot)",
            "assets": [
                {
                    "asset": "USDT",
                    "asset_name": "USDT",
                    "kind": "cash",
                    "qty": 3_860.0,
                    "available": 3_400.0,
                    "locked": 460.0,
                    "avg_price": 1_387.0,
                    "mark_price": 1_387.0,
                    "value_krw": 5_355_000.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "SOL",
                    "asset_name": "Solana",
                    "kind": "position",
                    "qty": 40.0,
                    "available": 40.0,
                    "locked": 0.0,
                    "avg_price": 224_000.0,
                    "mark_price": 229_800.0,
                    "value_krw": 9_192_000.0,
                    "pnl_krw": 232_000.0,
                },
                {
                    "asset": "BNB",
                    "asset_name": "BNB",
                    "kind": "position",
                    "qty": 3.5,
                    "available": 3.5,
                    "locked": 0.0,
                    "avg_price": 845_000.0,
                    "mark_price": 850_000.0,
                    "value_krw": 2_975_000.0,
                    "pnl_krw": 17_500.0,
                },
            ],
        },
        {
            "id": "binance_futures",
            "label": "바이낸스 선물",
            "market": "해외 가상자산 (Futures)",
            "assets": [
                {
                    "asset": "USDT-FUT",
                    "asset_name": "USDT (Futures)",
                    "kind": "cash",
                    "qty": 1_420.0,
                    "available": 1_260.0,
                    "locked": 160.0,
                    "avg_price": 1_387.0,
                    "mark_price": 1_387.0,
                    "value_krw": 1_969_540.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "SOLUSDT-PERP",
                    "asset_name": "SOLUSDT Perp",
                    "kind": "position",
                    "qty": 85.0,
                    "available": 85.0,
                    "locked": 0.0,
                    "avg_price": 231_000.0,
                    "mark_price": 229_800.0,
                    "value_krw": 19_533_000.0,
                    "pnl_krw": -102_000.0,
                },
            ],
        },
        {
            "id": "kr_stock",
            "label": "국장",
            "market": "KRX",
            "assets": [
                {
                    "asset": "KRW",
                    "asset_name": "KRW",
                    "kind": "cash",
                    "qty": 1_850_000.0,
                    "available": 1_850_000.0,
                    "locked": 0.0,
                    "avg_price": 1.0,
                    "mark_price": 1.0,
                    "value_krw": 1_850_000.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "005930",
                    "asset_name": "삼성전자",
                    "kind": "position",
                    "qty": 45.0,
                    "available": 45.0,
                    "locked": 0.0,
                    "avg_price": 74_900.0,
                    "mark_price": 75_800.0,
                    "value_krw": 3_411_000.0,
                    "pnl_krw": 40_500.0,
                },
                {
                    "asset": "000660",
                    "asset_name": "SK하이닉스",
                    "kind": "position",
                    "qty": 10.0,
                    "available": 10.0,
                    "locked": 0.0,
                    "avg_price": 187_000.0,
                    "mark_price": 190_000.0,
                    "value_krw": 1_900_000.0,
                    "pnl_krw": 30_000.0,
                },
            ],
        },
        {
            "id": "us_stock",
            "label": "미장",
            "market": "NASDAQ/NYSE",
            "assets": [
                {
                    "asset": "USD",
                    "asset_name": "USD",
                    "kind": "cash",
                    "qty": 1_670.0,
                    "available": 1_450.0,
                    "locked": 220.0,
                    "avg_price": 1_394.0,
                    "mark_price": 1_394.0,
                    "value_krw": 2_328_000.0,
                    "pnl_krw": 0.0,
                },
                {
                    "asset": "AAPL",
                    "asset_name": "Apple",
                    "kind": "position",
                    "qty": 8.0,
                    "available": 8.0,
                    "locked": 0.0,
                    "avg_price": 248_000.0,
                    "mark_price": 252_000.0,
                    "value_krw": 2_016_000.0,
                    "pnl_krw": 32_000.0,
                },
                {
                    "asset": "TSLA",
                    "asset_name": "Tesla",
                    "kind": "position",
                    "qty": 4.0,
                    "available": 4.0,
                    "locked": 0.0,
                    "avg_price": 296_000.0,
                    "mark_price": 301_000.0,
                    "value_krw": 1_204_000.0,
                    "pnl_krw": 20_000.0,
                },
            ],
        },
    ]

    portfolio_total_krw, cash_total_krw, invested_total_krw, unrealized_pnl_krw = recalculate_venue_totals(
        venues
    )

    sessions = [
        {
            "session_id": "upbit_scalper",
            "venue_id": "upbit",
            "venue_label": "업비트",
            "name": "단기 세션",
            "bot_name": "demo_upbit_scalper_v1",
            "mode": "short_term",
            "status": "RUNNING",
            "cycle_sec": 20,
            "active_markets": ["UPBIT"],
            "strategy_count": 2,
            "trade_count_today": 21,
            "realized_pnl_krw": 94_200.0,
            "unrealized_pnl_krw": 11_800.0,
            "fees_paid_krw": -10_300.0,
            "volume_traded_krw": 27_600_000.0,
            "trade_symbol": "BTC/KRW",
            "position_side": "LONG",
            "entry_price": 147_100_000.0,
            "stop_loss_price": 145_500_000.0,
            "take_profit_price": 149_400_000.0,
            "max_notional_krw": 3_500_000.0,
            "holding_limit_min": 45,
            "win_rate_pct": 59.2,
            "avg_holding_min": 16.4,
            "intraday_drawdown_pct": -1.2,
            "fee_breakdown": {
                "maker_krw": -3_700.0,
                "taker_krw": -6_600.0,
            },
            "display_note": "UPBIT net PnL is isolated from other exchanges",
            "risk_guard": "ON",
            "last_heartbeat": now,
        },
        {
            "session_id": "binance_scalper",
            "venue_id": "binance_futures",
            "venue_label": "바이낸스 선물",
            "name": "단기 세션",
            "bot_name": "demo_binance_scalper_v1",
            "mode": "short_term",
            "status": "RUNNING",
            "cycle_sec": 15,
            "active_markets": ["BINANCE_FUTURES"],
            "strategy_count": 2,
            "trade_count_today": 29,
            "realized_pnl_krw": 121_700.0,
            "unrealized_pnl_krw": 17_900.0,
            "fees_paid_krw": -12_900.0,
            "volume_traded_krw": 34_400_000.0,
            "trade_symbol": "SOL/USDT",
            "position_side": "LONG",
            "entry_price": 229_000.0,
            "stop_loss_price": 221_500.0,
            "take_profit_price": 236_800.0,
            "max_notional_krw": 4_200_000.0,
            "holding_limit_min": 30,
            "win_rate_pct": 63.0,
            "avg_holding_min": 12.7,
            "intraday_drawdown_pct": -1.9,
            "fee_breakdown": {
                "maker_krw": -4_400.0,
                "taker_krw": -8_500.0,
            },
            "display_note": "BINANCE futures fee/PnL tracked independently",
            "risk_guard": "ON",
            "last_heartbeat": now,
        },
        {
            "session_id": "upbit_balance",
            "venue_id": "upbit",
            "venue_label": "업비트",
            "name": "밸런스 세션",
            "bot_name": "demo_upbit_balance_v1",
            "mode": "mid_long_term",
            "status": "RUNNING",
            "cycle_sec": 1800,
            "active_markets": ["UPBIT"],
            "strategy_count": 1,
            "trade_count_today": 1,
            "portfolio_return_30d_pct": 2.1,
            "benchmark_return_30d_pct": 1.5,
            "tracking_error_30d_pct": 1.9,
            "max_drawdown_1y_pct": -7.1,
            "turnover_30d_pct": 2.8,
            "fee_drag_30d_pct": -0.09,
            "allocation_drift_pct": 1.8,
            "cash_buffer_pct": 8.3,
            "rebalance_due": "3일 후",
            "rebalance_amount_krw": 210_000.0,
            "rebalance_lines": 2,
            "targets": [
                {
                    "symbol": "BTC",
                    "target_weight_pct": 58.0,
                    "current_weight_pct": 55.2,
                    "target_price": 146_000_000.0,
                    "stop_loss_price": 138_000_000.0,
                    "take_profit_price": 158_000_000.0,
                },
                {
                    "symbol": "ETH",
                    "target_weight_pct": 42.0,
                    "current_weight_pct": 44.8,
                    "target_price": 5_200_000.0,
                    "stop_loss_price": 4_950_000.0,
                    "take_profit_price": 5_650_000.0,
                },
            ],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "1.8%", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "2.8%", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-0.09%", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "1.9%", "status": "OK"},
            ],
            "display_note": "UPBIT long-term allocation session",
            "risk_guard": "ON",
            "last_heartbeat": now,
        },
        {
            "session_id": "binance_balance",
            "venue_id": "binance",
            "venue_label": "바이낸스 현물",
            "name": "밸런스 세션",
            "bot_name": "demo_binance_balance_v1",
            "mode": "mid_long_term",
            "status": "RUNNING",
            "cycle_sec": 1800,
            "active_markets": ["BINANCE_SPOT"],
            "strategy_count": 1,
            "trade_count_today": 1,
            "portfolio_return_30d_pct": 3.4,
            "benchmark_return_30d_pct": 2.6,
            "tracking_error_30d_pct": 2.7,
            "max_drawdown_1y_pct": -10.4,
            "turnover_30d_pct": 4.9,
            "fee_drag_30d_pct": -0.13,
            "allocation_drift_pct": 3.4,
            "cash_buffer_pct": 12.1,
            "rebalance_due": "1일 후",
            "rebalance_amount_krw": 330_000.0,
            "rebalance_lines": 3,
            "targets": [
                {
                    "symbol": "SOL",
                    "target_weight_pct": 64.0,
                    "current_weight_pct": 60.9,
                    "target_price": 226_000.0,
                    "stop_loss_price": 214_000.0,
                    "take_profit_price": 246_000.0,
                },
                {
                    "symbol": "BNB",
                    "target_weight_pct": 36.0,
                    "current_weight_pct": 39.1,
                    "target_price": 845_000.0,
                    "stop_loss_price": 792_000.0,
                    "take_profit_price": 905_000.0,
                },
            ],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "3.4%", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "4.9%", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-0.13%", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "2.7%", "status": "OK"},
            ],
            "display_note": "BINANCE swing allocation session",
            "risk_guard": "ON",
            "last_heartbeat": now,
        },
        {
            "session_id": "krx_balance",
            "venue_id": "kr_stock",
            "venue_label": "국장",
            "name": "밸런스 세션",
            "bot_name": "demo_krx_balance_v1",
            "mode": "mid_long_term",
            "status": "RUNNING",
            "cycle_sec": 3600,
            "active_markets": ["KRX"],
            "strategy_count": 2,
            "trade_count_today": 0,
            "portfolio_return_30d_pct": 1.6,
            "benchmark_return_30d_pct": 1.2,
            "tracking_error_30d_pct": 1.1,
            "max_drawdown_1y_pct": -5.3,
            "turnover_30d_pct": 1.6,
            "fee_drag_30d_pct": -0.04,
            "allocation_drift_pct": 1.1,
            "cash_buffer_pct": 14.8,
            "rebalance_due": "5일 후",
            "rebalance_amount_krw": 140_000.0,
            "rebalance_lines": 1,
            "targets": [
                {
                    "symbol": "005930",
                    "target_weight_pct": 62.0,
                    "current_weight_pct": 60.4,
                    "target_price": 75_500.0,
                    "stop_loss_price": 70_800.0,
                    "take_profit_price": 79_300.0,
                },
                {
                    "symbol": "000660",
                    "target_weight_pct": 38.0,
                    "current_weight_pct": 39.6,
                    "target_price": 188_000.0,
                    "stop_loss_price": 179_000.0,
                    "take_profit_price": 199_000.0,
                },
            ],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "1.1%", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "1.6%", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-0.04%", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "1.1%", "status": "OK"},
            ],
            "display_note": "KRX rebalancing session (cash-heavy)",
            "risk_guard": "ON",
            "last_heartbeat": now,
        },
        {
            "session_id": "us_balance",
            "venue_id": "us_stock",
            "venue_label": "미장",
            "name": "밸런스 세션",
            "bot_name": "demo_us_balance_v1",
            "mode": "mid_long_term",
            "status": "RUNNING",
            "cycle_sec": 3600,
            "active_markets": ["NASDAQ", "NYSE"],
            "strategy_count": 2,
            "trade_count_today": 0,
            "portfolio_return_30d_pct": 2.4,
            "benchmark_return_30d_pct": 2.2,
            "tracking_error_30d_pct": 1.5,
            "max_drawdown_1y_pct": -6.2,
            "turnover_30d_pct": 2.0,
            "fee_drag_30d_pct": -0.06,
            "allocation_drift_pct": 1.9,
            "cash_buffer_pct": 10.7,
            "rebalance_due": "4일 후",
            "rebalance_amount_krw": 175_000.0,
            "rebalance_lines": 2,
            "targets": [
                {
                    "symbol": "AAPL",
                    "target_weight_pct": 54.0,
                    "current_weight_pct": 51.3,
                    "target_price": 250_000.0,
                    "stop_loss_price": 236_000.0,
                    "take_profit_price": 268_000.0,
                },
                {
                    "symbol": "TSLA",
                    "target_weight_pct": 46.0,
                    "current_weight_pct": 48.7,
                    "target_price": 299_000.0,
                    "stop_loss_price": 279_000.0,
                    "take_profit_price": 327_000.0,
                },
            ],
            "principles": [
                {"rule": "Allocation Drift <= 5%", "value": "1.9%", "status": "OK"},
                {"rule": "Turnover <= 8% (30d)", "value": "2.0%", "status": "OK"},
                {"rule": "Fee Drag >= -0.50% (30d)", "value": "-0.06%", "status": "OK"},
                {"rule": "Tracking Error <= 4% (30d)", "value": "1.5%", "status": "OK"},
            ],
            "display_note": "US equities session follows benchmark-aware rebalance",
            "risk_guard": "ON",
            "last_heartbeat": now,
        },
    ]

    return {
        "clock_utc": now,
        "portfolio_total_krw": portfolio_total_krw,
        "cash_total_krw": cash_total_krw,
        "invested_total_krw": invested_total_krw,
        "unrealized_pnl_krw": unrealized_pnl_krw,
        "venue_count": len(venues),
        "venues": venues,
        "sessions": sessions,
        "events": [
            {"type": "ui", "message": "세션을 거래소별로 독립 분리해서 표시"},
            {"type": "risk", "message": "리스크 엔진 연결 전: 관측 모드"},
            {"type": "telegram", "message": "Telegram 브릿지 연동 대기 중"},
        ],
    }
