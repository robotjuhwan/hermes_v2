from __future__ import annotations

import pytest

from tradecraft.services import market
from tradecraft.services.market import recalculate_venue_totals


def test_recalculate_venue_totals_prefers_broker_net_asset() -> None:
    venues = [
        {
            "id": "kr_stock",
            "assets": [
                {
                    "asset": "KRW",
                    "kind": "cash",
                    "value_krw": 4_132_803,
                    "net_asset_krw": 4_476_840,
                },
                {
                    "asset": "069500",
                    "kind": "position",
                    "value_krw": 123_350,
                    "pnl_krw": -310,
                },
                {
                    "asset": "091160",
                    "kind": "position",
                    "value_krw": 311_000,
                    "pnl_krw": 27_980,
                },
            ],
        }
    ]

    portfolio, cash, invested, pnl = recalculate_venue_totals(venues)

    assert venues[0]["total_krw"] == pytest.approx(4_476_840)
    assert venues[0]["total_value_krw"] == pytest.approx(4_476_840)
    assert venues[0]["total_asset_krw"] == pytest.approx(4_476_840)
    assert venues[0]["computed_total_krw"] == pytest.approx(4_567_153)
    assert venues[0]["broker_total_krw"] == pytest.approx(4_476_840)
    assert venues[0]["broker_total_value_krw"] == pytest.approx(4_476_840)
    assert venues[0]["total_value_basis"] == "broker_net_asset"
    assert portfolio == pytest.approx(4_476_840)
    assert cash == pytest.approx(4_132_803)
    assert invested == pytest.approx(434_350)
    assert pnl == pytest.approx(27_670)


def test_empty_dashboard_template_contains_no_sample_assets_or_demo_sessions() -> None:
    payload = market.empty_dashboard_template()

    assert {venue["id"] for venue in payload["venues"]} == {
        "upbit",
        "bithumb",
        "binance",
        "binance_futures",
        "kr_stock",
        "us_stock",
    }
    assert all(venue["assets"] == [] for venue in payload["venues"])
    assert payload["portfolio_total_krw"] == 0
    assert payload["cash_total_krw"] == 0
    assert payload["invested_total_krw"] == 0
    assert payload["total_krw"] == 0
    assert payload["cash_krw"] == 0
    assert payload["investment_krw"] == 0
    assert payload["unrealized_pnl_krw"] == 0
    assert payload["sessions"] == []
    assert not any(
        "demo" in str(value).lower()
        for row in payload["sessions"]
        for value in row.values()
    )


def test_recalculate_dashboard_totals_sets_legacy_total_aliases() -> None:
    dashboard = {
        "venues": [
            {
                "id": "kr_stock",
                "assets": [
                    {"asset": "KRW", "kind": "cash", "value_krw": 1000.0},
                    {"asset": "005930", "kind": "position", "value_krw": 2500.0},
                ],
            }
        ]
    }

    market.recalculate_dashboard_totals(dashboard)

    assert dashboard["portfolio_total_krw"] == pytest.approx(3500.0)
    assert dashboard["cash_total_krw"] == pytest.approx(1000.0)
    assert dashboard["invested_total_krw"] == pytest.approx(2500.0)
    assert dashboard["total_krw"] == pytest.approx(3500.0)
    assert dashboard["cash_krw"] == pytest.approx(1000.0)
    assert dashboard["investment_krw"] == pytest.approx(2500.0)
