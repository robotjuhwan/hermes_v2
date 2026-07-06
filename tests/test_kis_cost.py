from __future__ import annotations

from pathlib import Path

import pytest

from tradecraft.services.kis_cost import (
    is_kis_etf_for_cost,
    kis_close_cost_components,
    kis_closed_block_performance_metadata,
    kis_cost_feasibility_payload,
    kis_round_trip_cost_estimate,
)


def test_kis_block_trader_does_not_reown_cost_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _is_kis_etf_for_cost(" not in source
    assert "def _kis_round_trip_cost_estimate(" not in source
    assert "def _kis_close_cost_components(" not in source
    assert "def _kis_cost_feasibility_payload(" not in source


def test_kis_round_trip_cost_estimate_separates_fee_tax_slippage_and_spread() -> None:
    costs = kis_round_trip_cost_estimate(
        entry_price=100_000,
        exit_price=110_000,
        qty=1,
        is_etf=False,
        buy_fee_rate=0.00015,
        sell_fee_rate=0.00015,
        sell_tax_rate=0.002,
        slippage_bps=5.0,
        spread_bps=2.0,
    )

    assert costs["fees"] == pytest.approx(31.5)
    assert costs["taxes"] == pytest.approx(220.0)
    assert costs["slippage"] == pytest.approx(105.0)
    assert costs["spread"] == pytest.approx(42.0)
    assert costs["total"] == pytest.approx(398.5)


def test_kis_round_trip_cost_estimate_treats_etf_as_tax_exempt() -> None:
    costs = kis_round_trip_cost_estimate(
        entry_price=100_000,
        exit_price=110_000,
        qty=1,
        is_etf=True,
        buy_fee_rate=0.00015,
        sell_fee_rate=0.00015,
        sell_tax_rate=0.002,
        slippage_bps=0.0,
    )

    assert costs["taxes"] == 0.0
    assert costs["tax_exempt_reason"] == "etf"


def test_kis_close_cost_components_prefers_explicit_fee_tax_payloads() -> None:
    costs = kis_close_cost_components(
        entry_price=100_500,
        exit_price=110_500,
        qty=1,
        is_etf=False,
        buy_fee_rate=0.00015,
        sell_fee_rate=0.00015,
        sell_tax_rate=0.002,
        slippage_bps=5.0,
        spread_bps=0.0,
        payloads=[
            {
                "raw": {
                    "fee_krw": "20",
                    "tax_krw": "150",
                }
            }
        ],
    )

    assert costs["fees"] == pytest.approx(20)
    assert costs["taxes"] == pytest.approx(150)
    assert costs["slippage"] == pytest.approx(105.5)
    assert costs["total"] == pytest.approx(275.5)
    assert costs["status"] == "explicit_order_costs_plus_estimated_market_costs"
    assert costs["source"] == "kis_order_payload"
    assert costs["explicit_components"] == {"fees": 20.0, "taxes": 150.0}
    assert costs["estimated_components"]["fees"] == pytest.approx(31.65)
    assert costs["component_sources"] == {"fees": "fee_krw", "taxes": "tax_krw"}


def test_kis_closed_block_performance_metadata_uses_fill_costs_and_horizon() -> None:
    performance = kis_closed_block_performance_metadata(
        block={
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "entry_price": 100_500,
            "metadata": {"horizon": "mid"},
        },
        match={
            "avg_fill_price": 110_500,
            "raw": {
                "fee_krw": "20",
                "tax_krw": "150",
            },
        },
        filled_qty=1,
        order={"limit_price": 110_000, "reason": "target_reached"},
        buy_fee_rate=0.00015,
        sell_fee_rate=0.00015,
        sell_tax_rate=0.002,
        slippage_bps=5.0,
        spread_bps=0.0,
        recorded_at="2026-06-16T09:00:00+00:00",
    )

    assert performance["version"] == "kis_closed_block_performance_v1"
    assert performance["symbol"] == "277810"
    assert performance["horizon"] == "mid"
    assert performance["gross_pnl_krw"] == pytest.approx(10_000)
    assert performance["cost_components"] == {
        "fees": 20.0,
        "taxes": 150.0,
        "slippage": 105.5,
        "spread": 0.0,
        "funding": 0.0,
    }
    assert performance["total_cost_krw"] == pytest.approx(275.5)
    assert performance["net_pnl_krw"] == pytest.approx(9_724.5)
    assert performance["cost_model_status"] == "explicit_order_costs_plus_estimated_market_costs"
    assert performance["cost_source"] == "kis_order_payload"
    assert performance["recorded_at"] == "2026-06-16T09:00:00+00:00"
    assert performance["exit_reason"] == "target_reached"
    assert performance["explicit_components"] == {"fees": 20.0, "taxes": 150.0}


def test_kis_cost_feasibility_fails_when_target_move_cannot_clear_costs() -> None:
    feasibility = kis_cost_feasibility_payload(
        symbol="005930",
        name="삼성전자",
        entry_price=100_000,
        target_price=100_300,
        stop_price=99_000,
        qty=1,
        horizon="short",
        buy_fee_rate=0.00015,
        sell_fee_rate=0.00015,
        sell_tax_rate=0.002,
        slippage_bps=5.0,
        spread_bps=2.0,
    )

    assert feasibility["status"] == "fail"
    assert feasibility["is_etf"] is False
    assert feasibility["net_target_profit_after_cost_krw"] < 0


def test_is_kis_etf_for_cost_uses_horizon_and_name_prefixes() -> None:
    assert is_kis_etf_for_cost(symbol="069500", name="KODEX 200", horizon="short")
    assert is_kis_etf_for_cost(symbol="005930", name="삼성전자", horizon="core_etf")
    assert not is_kis_etf_for_cost(symbol="005930", name="삼성전자", horizon="short")
