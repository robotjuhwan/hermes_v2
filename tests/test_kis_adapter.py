from __future__ import annotations

import pytest

from tradecraft.services.kis import KISAdapter, KISConfig


def test_kis_to_assets_maps_balance_rows() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    rows = [
        {
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "hldg_qty": "12",
            "ord_psbl_qty": "10",
            "pchs_avg_pric": "70000",
            "prpr": "75000",
            "evlu_amt": "900000",
            "evlu_pfls_amt": "60000",
        },
        {
            "pdno": "000660",
            "hldg_qty": "0",
            "ord_psbl_qty": "0",
            "pchs_avg_pric": "180000",
            "prpr": "190000",
            "evlu_amt": "0",
            "evlu_pfls_amt": "0",
        },
    ]
    summary = {
        "dnca_tot_amt": "250000",
    }

    assets = adapter._to_assets(rows, summary)

    krw = next(item for item in assets if item["asset"] == "KRW")
    samsung = next(item for item in assets if item["asset"] == "005930")
    symbols = [item["asset"] for item in assets]

    assert krw["kind"] == "cash"
    assert krw["value_krw"] == pytest.approx(250_000.0)
    assert samsung["kind"] == "position"
    assert samsung["asset_name"] == "삼성전자"
    assert samsung["qty"] == pytest.approx(12.0)
    assert samsung["available"] == pytest.approx(10.0)
    assert samsung["locked"] == pytest.approx(2.0)
    assert samsung["value_krw"] == pytest.approx(900_000.0)
    assert samsung["pnl_krw"] == pytest.approx(60_000.0)
    assert "000660" not in symbols


def test_kis_domestic_order_tr_ids_use_current_cash_order_family() -> None:
    config = KISConfig()

    assert config.tr_id_order_buy == "TTTC0012U"
    assert config.tr_id_order_sell == "TTTC0011U"
    assert config.tr_id_order_revise_cancel == "TTTC0013U"
    assert config.tr_id_order_daily == "TTTC0081R"
    assert config.tr_id_order_cancelable == "TTTC0084R"


def test_kis_normalize_domestic_order_row_maps_fill_fields() -> None:
    adapter = KISAdapter(KISConfig())
    row = {
        "ord_dt": "20260508",
        "ord_gno_brno": "00123",
        "odno": "0000007777",
        "pdno": "005930",
        "prdt_name": "삼성전자",
        "sll_buy_dvsn_cd": "02",
        "ord_qty": "3",
        "ord_unpr": "70000",
        "tot_ccld_qty": "2",
        "tot_ccld_amt": "142000",
        "rmn_qty": "1",
        "psbl_qty": "1",
    }

    order = adapter.normalize_domestic_order_row(row)

    assert order["order_no"] == "0000007777"
    assert order["order_orgno"] == "00123"
    assert order["symbol"] == "005930"
    assert order["filled_qty"] == 2
    assert order["remaining_qty"] == 1
    assert order["cancelable_qty"] == 1
    assert order["avg_fill_price"] == pytest.approx(71_000.0)


def test_kis_select_cash_value_infers_from_total_minus_stocks() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    cash = adapter._select_cash_value(
        {
            "tot_evlu_amt": "5000000",
            "scts_evlu_amt": "4200000",
        }
    )
    assert cash == pytest.approx(800_000.0)


def test_kis_to_us_assets_maps_present_balance_rows() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    rows = [
        {
            "pdno": "AAPL",
            "ovrs_item_name": "Apple",
            "cblc_qty13": "3",
            "ord_psbl_qty1": "2",
            "avg_unpr3": "180",
            "ovrs_now_pric1": "190",
            "frcr_evlu_amt2": "570",
            "evlu_pfls_amt2": "30",
            "bass_exrt": "1300",
        },
    ]
    summary = {
        "frcr_dncl_amt_2": "100",
        "frst_bltn_exrt": "1300",
    }

    assets = adapter._to_us_assets(rows, summary)
    usd = next(item for item in assets if item["asset"] == "USD")
    aapl = next(item for item in assets if item["asset"] == "AAPL")

    assert usd["kind"] == "cash"
    assert usd["qty"] == pytest.approx(100.0)
    assert usd["value_krw"] == pytest.approx(130_000.0)
    assert aapl["kind"] == "position"
    assert aapl["asset_name"] == "Apple"
    assert aapl["available"] == pytest.approx(2.0)
    assert aapl["locked"] == pytest.approx(1.0)
    assert aapl["value_krw"] == pytest.approx(741_000.0)
    assert aapl["pnl_krw"] == pytest.approx(39_000.0)


def test_kis_select_usd_cash_value_falls_back_to_krw_summary() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    usd, krw = adapter._select_usd_cash_value({"tot_dncl_amt": "260000"}, 1300.0)
    assert usd == pytest.approx(200.0)
    assert krw == pytest.approx(260_000.0)


def test_kis_to_us_assets_uses_runtime_fx_override() -> None:
    adapter = KISAdapter(
        KISConfig(
            app_key="app",
            app_secret="secret",
            account_no="12345678",
            product_code="01",
        )
    )
    rows = [
        {
            "pdno": "AAPL",
            "ovrs_item_name": "Apple",
            "cblc_qty13": "2",
            "ord_psbl_qty1": "2",
            "avg_unpr3": "100",
            "ovrs_now_pric1": "110",
            "frcr_evlu_amt2": "220",
            "evlu_pfls_amt2": "20",
            "bass_exrt": "1300",
        },
    ]
    summary = {
        "frcr_dncl_amt_2": "10",
        "frst_bltn_exrt": "1300",
    }

    assets = adapter._to_us_assets(rows, summary, usd_krw_rate=1500.0)
    usd = next(item for item in assets if item["asset"] == "USD")
    aapl = next(item for item in assets if item["asset"] == "AAPL")
    assert usd["mark_price"] == pytest.approx(1500.0)
    assert aapl["mark_price"] == pytest.approx(165000.0)
