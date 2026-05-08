from __future__ import annotations

from pathlib import Path

from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsRepository,
    parse_naver_coinfo_html,
    parse_wisereport_financials,
    score_valuation,
)


def test_parse_naver_coinfo_html_extracts_core_valuation() -> None:
    raw_html = """
    <html>
      <head><title>삼성전자 : Npay 증권</title></head>
      <body>
        <dl class="blind">
          <dd>종목명 삼성전자</dd>
          <dd>현재가 266,000 전일대비 상승</dd>
        </dl>
        <em id="_market_sum">1,555조 1,101</em>
        <em id="_cns_per">7.00</em>
        <em id="_cns_eps">40,286</em>
        <table>
          <tr>
            <th>PBR</th>
            <td><em>N/A</em><span>l</span><em>63,997</em>원</td>
          </tr>
        </table>
        <em id="_dvr">0.63</em>
        <p>동일업종 PER <em>28.90</em>배</p>
      </body>
    </html>
    """

    payload = parse_naver_coinfo_html(
        raw_html,
        symbol="005930",
        source_url="https://finance.naver.com/item/coinfo.naver?code=005930",
    )

    assert payload["symbol"] == "005930"
    assert payload["name"] == "삼성전자"
    assert payload["price"] == 266_000
    assert payload["market_cap_krw"] == 1_555_110_100_000_000
    assert payload["per"] == 7.0
    assert payload["eps"] == 40_286
    assert payload["pbr"] == 4.16
    assert payload["bps"] == 63_997
    assert payload["dividend_yield_pct"] == 0.63
    assert payload["industry_per"] == 28.9


def test_parse_wisereport_financials_extracts_known_rows() -> None:
    raw_html = """
    <table>
      <tr><th>IFRS(연결)</th><th>2024/12</th><th>2025/12</th></tr>
      <tr><th>매출액</th><td>300,000</td><td>330,000</td></tr>
      <tr><th>영업이익</th><td>35,000</td><td>42,000</td></tr>
      <tr><th>당기순이익</th><td>28,000</td><td>36,000</td></tr>
      <tr><th>ROE</th><td>9.4</td><td>11.1</td></tr>
      <tr><th>부채비율</th><td>28.0</td><td>30.5</td></tr>
      <tr><th>영업이익률</th><td>11.7</td><td>12.7</td></tr>
    </table>
    """

    rows = parse_wisereport_financials(raw_html, symbol="005930")

    assert len(rows) == 2
    assert rows[0]["symbol"] == "005930"
    assert rows[0]["period"] == "2024/12"
    assert rows[0]["period_type"] == "annual"
    assert rows[0]["revenue"] == 300_000
    assert rows[0]["operating_profit"] == 35_000
    assert rows[0]["roe"] == 9.4
    assert rows[1]["net_income"] == 36_000


def test_parse_wisereport_financials_returns_empty_for_unparseable_html() -> None:
    assert parse_wisereport_financials("<div>no table</div>", symbol="005930") == []


def test_score_valuation_labels_discount_as_undervalued() -> None:
    score = score_valuation(
        {
            "symbol": "005930",
            "per": 7.0,
            "eps": 40_286,
            "pbr": 0.9,
            "bps": 63_997,
            "dividend_yield_pct": 1.4,
            "industry_per": 28.9,
        },
        financials=[
            {
                "operating_profit": 35_000,
                "operating_margin": 12.0,
                "roe": 9.0,
            }
        ],
    )

    assert score["label"] == "undervalued"
    assert score["undervalued_score"] >= 65
    assert score["quality_score"] > 50
    assert score["relative_per_discount_pct"] > 70
    assert any("업종 PER" in row for row in score["reasons"])


def test_repository_round_trip_and_error_state(tmp_path: Path) -> None:
    repository = SymbolFundamentalsRepository(str(tmp_path / "symbol_fundamentals.db"))
    valuation = {
        "symbol": "005930",
        "name": "삼성전자",
        "price": 80_000,
        "market_cap_krw": 470_000_000_000_000,
        "per": 9.5,
        "eps": 8400,
        "pbr": 1.1,
        "bps": 72_000,
        "dividend_yield_pct": 1.7,
        "industry_per": 20.0,
        "industry_name": "반도체",
        "as_of": "2026-05-06",
        "source_url": "https://finance.naver.com/item/coinfo.naver?code=005930",
        "raw": {"fixture": True},
        "crawled_at": "2026-05-06T00:00:00+00:00",
        "status": "ok",
    }

    repository.upsert_snapshot(
        valuation,
        financials=[
            {
                "symbol": "005930",
                "period_type": "annual",
                "period": "2025/12",
                "revenue": 330_000,
                "operating_profit": 42_000,
                "net_income": 36_000,
                "roe": 11.1,
                "debt_ratio": 30.5,
                "operating_margin": 12.7,
                "raw": {"fixture": True},
            }
        ],
    )

    latest = repository.latest("005930")
    assert latest is not None
    assert latest["status"] == "ok"
    assert latest["valuation"]["name"] == "삼성전자"
    assert latest["score"]["label"] in {"undervalued", "fair"}
    assert latest["financials"][0]["period"] == "2025/12"

    repository.record_error("000660", "fixture failure")
    errored = repository.latest("000660")
    assert errored is not None
    assert errored["status"] == "error"
    assert "fixture failure" in errored["error_message"]
    status = repository.status(min_refresh_hours=12)
    assert status["error_count"] == 1
    assert status["total_symbols"] == 2
    assert status["ok_symbol_count"] == 1
    assert status["error_symbol_count"] == 1
    assert "stale_symbol_count" in status
