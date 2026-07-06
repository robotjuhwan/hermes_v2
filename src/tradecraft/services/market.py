from __future__ import annotations

from datetime import datetime, timezone


DEFAULT_DASHBOARD_VENUES: tuple[dict[str, str], ...] = (
    {"id": "upbit", "label": "업비트", "market": "국내 가상자산"},
    {"id": "bithumb", "label": "빗썸", "market": "국내 가상자산"},
    {"id": "binance", "label": "바이낸스 현물", "market": "해외 가상자산 (Spot)"},
    {
        "id": "binance_futures",
        "label": "바이낸스 선물",
        "market": "해외 가상자산 (Futures)",
    },
    {"id": "kr_stock", "label": "국장1", "market": "KRX"},
    {"id": "us_stock", "label": "미장", "market": "NASDAQ/NYSE"},
)


def recalculate_venue_totals(venues: list[dict]) -> tuple[float, float, float, float]:
    portfolio_total_krw = 0.0
    cash_total_krw = 0.0
    invested_total_krw = 0.0
    unrealized_pnl_krw = 0.0

    for venue in venues:
        assets = list(venue.get("assets", []))
        cash = sum(
            float(a.get("value_krw", 0.0)) for a in assets if a.get("kind") == "cash"
        )
        holdings = sum(
            float(a.get("value_krw", 0.0)) for a in assets if a.get("kind") != "cash"
        )
        pnl = sum(
            float(a.get("pnl_krw", 0.0)) for a in assets if a.get("kind") != "cash"
        )
        computed_total = cash + holdings
        broker_total = max(
            (
                float(a.get("net_asset_krw") or a.get("total_value_krw") or 0.0)
                for a in assets
                if a.get("kind") == "cash"
            ),
            default=0.0,
        )
        total = broker_total if broker_total > 0 else computed_total

        venue["cash_krw"] = cash
        venue["invested_krw"] = holdings
        venue["unrealized_pnl_krw"] = pnl
        venue["total_krw"] = total
        venue["total_value_krw"] = total
        venue["total_asset_krw"] = total
        venue["computed_total_krw"] = computed_total
        venue["broker_total_krw"] = broker_total
        venue["broker_total_value_krw"] = broker_total
        venue["total_value_basis"] = (
            "broker_net_asset" if broker_total > 0 else "cash_plus_positions"
        )

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
    dashboard["total_krw"] = portfolio
    dashboard["cash_krw"] = cash
    dashboard["investment_krw"] = invested
    dashboard["unrealized_pnl_krw"] = pnl
    dashboard["venue_count"] = len(venues)
    return dashboard


def normalize_dashboard_assets(assets: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        row = dict(asset)
        asset_code = str(row.get("asset") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        if asset_code and not symbol:
            row["symbol"] = asset_code
        normalized.append(row)
    return normalized


def replace_venue_assets(dashboard: dict, venue_id: str, assets: list[dict]) -> bool:
    normalized_assets = normalize_dashboard_assets(assets)
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["assets"] = normalized_assets
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
    normalized_assets = normalize_dashboard_assets(assets)
    for venue in dashboard.get("venues", []):
        if venue.get("id") != venue_id:
            continue
        venue["label"] = label
        venue["market"] = market
        venue["assets"] = normalized_assets
        recalculate_dashboard_totals(dashboard)
        return

    venues = dashboard.setdefault("venues", [])
    venues.append(
        {
            "id": venue_id,
            "label": label,
            "market": market,
            "assets": normalized_assets,
        }
    )
    recalculate_dashboard_totals(dashboard)


def empty_dashboard_template() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    venues = [
        {
            **venue,
            "assets": [],
        }
        for venue in DEFAULT_DASHBOARD_VENUES
    ]
    dashboard = {
        "clock_utc": now,
        "portfolio_total_krw": 0.0,
        "cash_total_krw": 0.0,
        "invested_total_krw": 0.0,
        "total_krw": 0.0,
        "cash_krw": 0.0,
        "investment_krw": 0.0,
        "unrealized_pnl_krw": 0.0,
        "venue_count": len(venues),
        "venues": venues,
        "sessions": [],
        "events": [
            {"type": "ui", "message": "거래소별 실잔고를 빈 템플릿에서 조립"},
            {"type": "risk", "message": "리스크 게이트와 검증 상태는 운영 상태에서 추적"},
            {"type": "telegram", "message": "Telegram 브릿지 상태는 운영 API에서 추적"},
        ],
    }
    return recalculate_dashboard_totals(dashboard)
