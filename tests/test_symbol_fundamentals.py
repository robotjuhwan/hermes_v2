from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsConfig,
    SymbolFundamentalsRepository,
    SymbolFundamentalsService,
    jue_wiki_repair_target_symbols,
    merge_fundamental_target_symbols,
    parse_naver_coinfo_html,
    parse_wisereport_financials,
    resolve_jue_wiki_fundamental_repair_actions,
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


def test_jue_wiki_repair_target_symbols_reads_scheduled_fundamental_repairs(
    tmp_path: Path,
) -> None:
    wiki_db = tmp_path / "jue_wiki" / "wiki.db"
    wiki_db.parent.mkdir(parents=True)
    with sqlite3.connect(wiki_db) as conn:
        conn.execute(
            """
            CREATE TABLE wiki_repair_actions (
                action_id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at
            ) VALUES (
                'repair:1', 'evidence_quality:1', 'kis.symbol.005930',
                'refresh_symbol_financials', 'scheduled',
                '{"symbols":["005930","000660"],"source_type":"symbol_fundamentals"}',
                '2026-07-03T01:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at
            ) VALUES (
                'repair:2', 'evidence_quality:2', 'kis.symbol.402340',
                'refresh_symbol_fundamentals', 'unresolved',
                '{"symbols":["402340","BAD"],"source_type":"symbol_fundamentals"}',
                '2026-07-03T01:01:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at
            ) VALUES (
                'repair:old', 'evidence_quality:old', 'kis.symbol.123456',
                'refresh_symbol_fundamentals', 'resolved',
                '{"symbols":["123456"],"source_type":"symbol_fundamentals"}',
                '2026-07-03T00:00:00+00:00'
            )
            """
        )

    assert jue_wiki_repair_target_symbols(wiki_db, limit=10) == [
        "402340",
        "005930",
        "000660",
    ]


def test_jue_wiki_repair_target_symbols_ignores_missing_db(tmp_path: Path) -> None:
    assert jue_wiki_repair_target_symbols(tmp_path / "missing.db") == []


def test_merge_fundamental_target_symbols_prioritizes_repair_targets() -> None:
    assert merge_fundamental_target_symbols(
        watchlist=["005930", "bad"],
        repair_targets=["402340", "005930", "000660"],
        discovered=["178920", "402340"],
        limit=4,
    ) == ["005930", "402340", "000660", "178920"]


def test_resolve_jue_wiki_fundamental_repairs_requires_matching_data(
    tmp_path: Path,
) -> None:
    wiki_db = tmp_path / "jue_wiki" / "wiki.db"
    wiki_db.parent.mkdir(parents=True)
    with sqlite3.connect(wiki_db) as conn:
        conn.execute(
            """
            CREATE TABLE wiki_repair_actions (
                action_id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at
            ) VALUES (
                'repair:financials', 'evidence_quality:financials',
                'kis.symbol.005930', 'refresh_symbol_financials', 'scheduled',
                '{"symbols":["005930"],"quality_warnings":["financials_missing"]}',
                '2026-07-03T01:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at
            ) VALUES (
                'repair:still-missing', 'evidence_quality:still-missing',
                'kis.symbol.000660', 'refresh_symbol_financials', 'scheduled',
                '{"symbols":["000660"],"quality_warnings":["financials_missing"]}',
                '2026-07-03T01:01:00+00:00'
            )
            """
        )

    result = resolve_jue_wiki_fundamental_repair_actions(
        wiki_db,
        latest_by_symbol={
            "005930": {
                "status": "ok",
                "valuation": {"price": 70000, "per": 12.1},
                "financials": [{"period": "2026/03", "revenue": 1000.0}],
            },
            "000660": {
                "status": "ok",
                "valuation": {"price": 120000, "per": 9.2},
                "financials": [],
            },
        },
        resolved_at="2026-07-03T02:00:00+00:00",
    )

    assert result == {
        "status": "ok",
        "resolved_count": 1,
        "checked_count": 2,
        "resolved_action_ids": ["repair:financials"],
    }
    with sqlite3.connect(wiki_db) as conn:
        rows = conn.execute(
            """
            SELECT action_id, status, finished_at, details_json
            FROM wiki_repair_actions
            ORDER BY action_id
            """
        ).fetchall()

    assert rows[0][0] == "repair:financials"
    assert rows[0][1] == "resolved"
    assert rows[0][2] == "2026-07-03T02:00:00+00:00"
    assert '"resolved_by": "symbol_fundamentals_collect"' in rows[0][3]
    assert rows[1][0] == "repair:still-missing"
    assert rows[1][1] == "scheduled"


def test_collect_symbols_resolves_jue_wiki_repair_actions(tmp_path: Path) -> None:
    wiki_db = tmp_path / "jue_wiki" / "wiki.db"
    wiki_db.parent.mkdir(parents=True)
    with sqlite3.connect(wiki_db) as conn:
        conn.execute(
            """
            CREATE TABLE wiki_repair_actions (
                action_id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wiki_repair_actions (
                action_id, finding_id, page_id, action_type, status,
                details_json, created_at
            ) VALUES (
                'repair:financials', 'evidence_quality:financials',
                'kis.symbol.005930', 'refresh_symbol_financials', 'scheduled',
                '{"symbols":["005930"],"quality_warnings":["financials_missing"]}',
                '2026-07-03T01:00:00+00:00'
            )
            """
        )
    service = SymbolFundamentalsService(
        SymbolFundamentalsConfig(
            db_path=str(tmp_path / "symbol_fundamentals.db"),
            jue_wiki_db_path=str(wiki_db),
        )
    )

    async def fake_collect_one(_client, symbol: str) -> dict:
        valuation = {
            "symbol": symbol,
            "name": "삼성전자",
            "price": 70000,
            "per": 12.1,
            "as_of": "2026-07-03",
            "status": "ok",
            "crawled_at": "2026-07-03T02:00:00+00:00",
            "last_attempt_at": "2026-07-03T02:00:00+00:00",
        }
        financials = [
            {
                "symbol": symbol,
                "period_type": "quarterly",
                "period": "2026/03",
                "revenue": 1000.0,
            }
        ]
        service.repository.upsert_snapshot(valuation, financials=financials)
        latest = service.repository.latest(symbol)
        assert latest is not None
        return latest

    service._collect_one = fake_collect_one  # type: ignore[method-assign]

    result = asyncio.run(service.collect_symbols(["005930"], force=True))

    assert result["status"] == "ok"
    assert result["jue_wiki_repair_resolution"]["resolved_action_ids"] == [
        "repair:financials"
    ]
    with sqlite3.connect(wiki_db) as conn:
        row = conn.execute(
            """
            SELECT status, finished_at
            FROM wiki_repair_actions
            WHERE action_id = 'repair:financials'
            """
        ).fetchone()
    assert row[0] == "resolved"
    assert row[1]


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


def test_parse_wisereport_financials_ignores_credit_rating_tables() -> None:
    raw_html = """
    <table>
      <tr><th>신용등급</th><th>AA+ [20260311]</th><th>A1 [20260514]</th></tr>
      <tr><th>컨센서스</th><td>&gt;</td><td>&gt;</td></tr>
      <tr><th>영업이익/매출액(수익)</th><td>컨센서스</td><td>컨센서스</td></tr>
    </table>
    <table>
      <tr><th>IFRS(연결)</th><th>2024/12</th><th>2025/12(E)</th></tr>
      <tr><th>매출액</th><td>300,000</td><td>330,000</td></tr>
      <tr><th>영업이익</th><td>35,000</td><td>42,000</td></tr>
      <tr><th>당기순이익</th><td>28,000</td><td>36,000</td></tr>
      <tr><th>ROE</th><td>9.4</td><td>11.1</td></tr>
    </table>
    """

    rows = parse_wisereport_financials(raw_html, symbol="005930")

    assert [row["period"] for row in rows] == ["2024/12", "2025/12(E)"]
    assert rows[0]["revenue"] == 300_000
    assert rows[0]["operating_profit"] == 35_000
    assert rows[0]["raw"]["revenue"] == "300,000"
    assert all("AA+" not in row["period"] for row in rows)


def test_parse_wisereport_financials_rejects_credit_rating_only_table() -> None:
    raw_html = """
    <table>
      <tr><th>신용등급</th><th>AA+ [20260311]</th><th>A1 [20260514]</th></tr>
      <tr><th>영업이익/매출액(수익)</th><td>2.0</td><td></td></tr>
      <tr><th>ROE</th><td>2.0</td><td></td></tr>
    </table>
    """

    rows = parse_wisereport_financials(raw_html, symbol="005930")

    assert rows == []


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


def test_score_valuation_treats_etf_as_non_company_valuation() -> None:
    score = score_valuation(
        {
            "symbol": "091160",
            "name": "KODEX 반도체",
            "raw": {"asset_class": "etf"},
            "price": 16_830,
            "market_cap_krw": 732_950_000_000,
        },
        financials=[],
    )

    assert score["label"] == "unknown"
    assert score["undervalued_score"] == 0
    assert score["quality_score"] == 0
    assert any("ETF" in row for row in score["risks"])


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
    assert status["fresh_symbol_count"] == 0
    assert "stale_symbol_count" in status
    assert "stale_ratio" in status


def test_repository_status_does_not_count_recent_errors_as_fresh(
    tmp_path: Path,
) -> None:
    repository = SymbolFundamentalsRepository(str(tmp_path / "symbol_fundamentals.db"))
    now = datetime.now(timezone.utc).isoformat()
    repository.upsert_snapshot(
        {
            "symbol": "005930",
            "name": "삼성전자",
            "as_of": now[:10],
            "crawled_at": now,
            "status": "ok",
            "raw": {},
        }
    )
    repository.record_error("000660", "fixture failure")

    status = repository.status(min_refresh_hours=12)

    assert status["total_symbols"] == 2
    assert status["ok_symbol_count"] == 1
    assert status["error_symbol_count"] == 1
    assert status["fresh_symbol_count"] == 1
    assert status["stale_symbol_count"] == 0


def test_repository_status_latest_symbols_are_ordered_by_latest_crawl(
    tmp_path: Path,
) -> None:
    repository = SymbolFundamentalsRepository(str(tmp_path / "symbol_fundamentals.db"))
    repository.upsert_snapshot(
        {
            "symbol": "000001",
            "name": "오래된낮은코드",
            "as_of": "2026-05-01",
            "crawled_at": "2026-05-01T00:00:00+00:00",
            "status": "ok",
            "raw": {},
        }
    )
    repository.upsert_snapshot(
        {
            "symbol": "999999",
            "name": "최신높은코드",
            "as_of": "2026-06-01",
            "crawled_at": "2026-06-01T00:00:00+00:00",
            "status": "ok",
            "raw": {},
        }
    )

    status = repository.status()

    assert status["latest_symbols"][0]["symbol"] == "999999"
    assert status["latest_symbols"][0]["name"] == "최신높은코드"


def test_repository_status_reports_latest_symbol_freshness_separately(
    tmp_path: Path,
) -> None:
    repository = SymbolFundamentalsRepository(str(tmp_path / "symbol_fundamentals.db"))
    old_at = "2026-05-01T00:00:00+00:00"
    fresh_at = datetime.now(timezone.utc).isoformat()
    for idx in range(20):
        repository.upsert_snapshot(
            {
                "symbol": f"1{idx:05d}",
                "name": f"오래된종목{idx}",
                "as_of": old_at[:10],
                "crawled_at": old_at,
                "status": "ok",
                "raw": {},
            }
        )
    for idx in range(3):
        repository.upsert_snapshot(
            {
                "symbol": f"9{idx:05d}",
                "name": f"최신종목{idx}",
                "as_of": fresh_at[:10],
                "crawled_at": fresh_at,
                "status": "ok",
                "raw": {},
            }
        )

    status = repository.status(min_refresh_hours=12)

    assert status["stale_ratio"] > 0.8
    assert status["latest_symbols_count"] == 8
    assert status["latest_symbols_fresh_count"] == 3
    assert status["latest_symbols_stale_count"] == 5
    assert status["latest_symbols_stale_ratio"] == 5 / 8
