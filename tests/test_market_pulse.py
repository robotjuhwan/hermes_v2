from __future__ import annotations

import asyncio
import base64
import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from tradecraft.services.market_pulse import (
    MarketPulseConfig,
    MarketPulseRepository,
    MarketPulseService,
    build_futures_basis,
    parse_naver_fx_html,
    parse_naver_investor_flow_html,
    parse_naver_index_html,
    parse_naver_program_trading_html,
)


def _decode_gzip_base64(value: str) -> str:
    assert value.startswith("gzip+base64:")
    return gzip.decompress(base64.b64decode(value.removeprefix("gzip+base64:"))).decode(
        "utf-8"
    )


class _FakeSignals:
    def list_external_signals(self, **kwargs) -> dict:
        _ = kwargs
        return {
            "status": "ok",
            "latest_collected_at": "2026-05-08T23:53:00+09:00",
            "items": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "direction": "positive",
                    "strength": 82,
                    "summary": "세시반 선도 섹터 1위 반도체: 삼성전자 등락률 2.10%, 거래대금 기여 18.00%, 섹터 수익률 1.70%",
                    "as_of": "2026-05-08T23:53:00+09:00",
                    "tags": ["sesiban", "sector_treemap", "after_close_flow"],
                },
                {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "direction": "positive",
                    "strength": 88,
                    "summary": "세시반 선도 섹터 1위 반도체: SK하이닉스 등락률 3.20%, 거래대금 기여 21.00%, 섹터 수익률 1.70%",
                    "as_of": "2026-05-08T23:53:00+09:00",
                    "tags": ["sesiban", "sector_treemap", "after_close_flow"],
                },
            ],
        }


def test_market_pulse_repository_uses_wal_and_busy_timeout(tmp_path) -> None:
    repository = MarketPulseRepository(tmp_path / "market_pulse.db")

    with repository._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_market_pulse_prune_history_deletes_old_snapshots(tmp_path) -> None:
    repository = MarketPulseRepository(tmp_path / "market_pulse.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    repository.save_snapshot(
        {
            "captured_at": (now - timedelta(days=10)).isoformat(),
            "trading_day": "2026-06-05",
            "status": "ok",
            "regime": "risk_on",
            "score": 72,
            "raw_marker": "old_market_pulse",
        }
    )
    repository.save_snapshot(
        {
            "captured_at": now.isoformat(),
            "trading_day": "2026-06-15",
            "status": "ok",
            "regime": "risk_off",
            "score": 45,
        }
    )

    result = repository.prune_history(retention_days=7, now=now)

    assert result["snapshot_deleted"] == 1
    assert result["archived"]["market_pulse_snapshots"] == 1
    assert repository.status()["snapshot_count"] == 1
    assert repository.latest()["regime"] == "risk_off"
    with repository._connect() as conn:
        archived_raw = conn.execute(
            "SELECT raw_json FROM market_pulse_snapshots_archive"
        ).fetchone()[0]
    assert json.loads(_decode_gzip_base64(archived_raw))["raw_marker"] == (
        "old_market_pulse"
    )


def test_market_pulse_prune_history_prunes_old_archive(tmp_path) -> None:
    repository = MarketPulseRepository(tmp_path / "market_pulse.db")
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    for days, marker in [
        (20, "cold_market_pulse"),
        (10, "warm_market_pulse"),
        (0, "fresh_market_pulse"),
    ]:
        repository.save_snapshot(
            {
                "captured_at": (now - timedelta(days=days)).isoformat(),
                "trading_day": "2026-06-30",
                "status": "ok",
                "regime": "risk_on",
                "score": 72,
                "raw_marker": marker,
            }
        )

    result = repository.prune_history(
        retention_days=7,
        archive_retention_days=14,
        now=now,
    )

    assert result["snapshot_deleted"] == 2
    assert result["archived"]["market_pulse_snapshots"] == 2
    assert result["archive_deleted"]["market_pulse_snapshots_archive"] == 1
    assert result["vacuumed"] is True
    with repository._connect() as conn:
        archived_raw = conn.execute(
            "SELECT raw_json FROM market_pulse_snapshots_archive"
        ).fetchone()[0]
    assert json.loads(_decode_gzip_base64(archived_raw))["raw_marker"] == (
        "warm_market_pulse"
    )


def test_market_pulse_prune_history_compacts_old_active_raw_payloads(tmp_path) -> None:
    repository = MarketPulseRepository(tmp_path / "market_pulse.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    old_at = (now - timedelta(hours=2)).isoformat()
    recent_at = now.isoformat()
    repository.save_snapshot(
        {
            "captured_at": old_at,
            "trading_day": "2026-06-15",
            "status": "ok",
            "regime": "risk_on",
            "score": 72,
            "indices": [{"code": "KOSPI", "value": 3000}],
            "sectors": {"leaders": [{"name": "반도체"}]},
            "raw_blob": "x" * 5000,
        }
    )
    repository.save_snapshot(
        {
            "captured_at": recent_at,
            "trading_day": "2026-06-15",
            "status": "ok",
            "regime": "risk_off",
            "score": 42,
            "raw_blob": "y" * 5000,
        }
    )

    result = repository.prune_history(
        retention_days=7,
        now=now,
        compact_recent_snapshot_count=1,
        compact_raw_min_chars=100,
    )

    assert result["compacted"]["market_pulse_snapshots"] == 1
    with repository._connect() as conn:
        old_raw = conn.execute(
            "SELECT raw_json FROM market_pulse_snapshots WHERE captured_at = ?",
            (old_at,),
        ).fetchone()[0]
        recent_raw = conn.execute(
            "SELECT raw_json FROM market_pulse_snapshots WHERE captured_at = ?",
            (recent_at,),
        ).fetchone()[0]
    assert json.loads(old_raw)["compacted"] is True
    assert json.loads(recent_raw)["raw_blob"] == "y" * 5000

    items = repository.history(limit=2)["items"]
    old_item = next(row for row in items if row["captured_at"] == old_at)
    assert old_item["regime"] == "risk_on"
    assert old_item["indices"] == [{"code": "KOSPI", "value": 3000}]
    assert old_item["sectors"] == {"leaders": [{"name": "반도체"}]}


def test_market_pulse_prune_history_compresses_archive_columns(tmp_path) -> None:
    repository = MarketPulseRepository(tmp_path / "market_pulse.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    repository.save_snapshot(
        {
            "captured_at": (now - timedelta(days=10)).isoformat(),
            "trading_day": "2026-06-05",
            "status": "ok",
            "regime": "risk_on",
            "score": 72,
            "indices": [{"code": "KOSPI", "value": 3000, "blob": "i" * 500}],
            "sectors": {"leaders": [{"name": "반도체", "blob": "s" * 500}]},
            "risk_flags": ["flag"],
            "data_gaps": ["gap"],
            "raw_marker": "old_market_pulse",
        }
    )

    result = repository.prune_history(retention_days=7, now=now)

    assert result["archive_compacted"]["market_pulse_snapshots_archive"]["compacted"] == 1
    with repository._connect() as conn:
        row = conn.execute(
            """
            SELECT indices_json, sector_json, risk_flags_json, data_gaps_json, raw_json
            FROM market_pulse_snapshots_archive
            """
        ).fetchone()

    for value in row:
        assert value.startswith("gzip+base64:")
    assert json.loads(_decode_gzip_base64(row[0])) == [
        {"blob": "i" * 500, "code": "KOSPI", "value": 3000}
    ]
    assert json.loads(_decode_gzip_base64(row[1])) == {
        "leaders": [{"blob": "s" * 500, "name": "반도체"}]
    }


def test_parse_naver_index_html_handles_main_index_layout() -> None:
    row = parse_naver_index_html(
        """
        <div class="quotient up" id ="quotient">
          <em id="now_value">7,498.00</em>
          <span class="fluc" id="change_value_and_rate"><span>7.95</span> +0.11%<span class="blind">상승</span></span>
        </div>
        """,
        code="KOSPI",
    )

    assert row["status"] == "ok"
    assert row["value"] == 7498.0
    assert row["change"] == 7.95
    assert row["change_pct"] == 0.11
    assert row["direction"] == "up"


def test_parse_naver_index_html_handles_kpi200_table_layout() -> None:
    row = parse_naver_index_html(
        """
        <td class="imp_number" id="now_value"><strong class="blue01">1,151.17</strong></td>
        <td class="imp_txt" id="change_value">
          <img alt="하락"><span class="tah p11 blue02">1.31</span>
        </td>
        <td class="imp_txt" id="change_rate"><strong class="tah blue01">-0.11%</strong></td>
        """,
        code="KPI200",
    )

    assert row["name"] == "KOSPI200"
    assert row["value"] == 1151.17
    assert row["change"] == -1.31
    assert row["change_pct"] == -0.11
    assert row["direction"] == "down"


def test_parse_naver_index_html_handles_futures_layout() -> None:
    row = parse_naver_index_html(
        """
        <tr>
          <th class="imp_txt">선물(2606)</th>
          <td class="imp_number" id="now_value"><strong class="nv01">1,153.25</strong></td>
        </tr>
        <tr>
          <th>전일대비</th>
          <td class="imp_txt" id="change_value"><span>2.45</span><span class="blind">상승</span></td>
          <td class="imp_txt" id="change_rate"><strong>+0.21%</strong></td>
        </tr>
        """,
        code="FUT",
    )

    assert row["name"] == "KOSPI200 선물"
    assert row["value"] == 1153.25
    assert row["change"] == 2.45
    assert row["change_pct"] == 0.21
    assert row["direction"] == "up"


def test_parse_naver_investor_flow_html_extracts_latest_row() -> None:
    row = parse_naver_investor_flow_html(
        """
        <tr class="udline">
          <th>시간</th><th>개인</th><th>외국인</th><th>기관계</th><th>금융투자</th>
          <th>보험</th><th>투신<br>(사모)</th><th>은행</th><th>기타금융기관</th><th>연기금등</th><th>기타법인</th>
        </tr>
        <tr>
          <td>18:06</td><td>39,707</td><td>-52,967</td><td>12,542</td><td>15,166</td>
          <td>-826</td><td>-1,357</td><td>-6</td><td>67</td><td>-502</td><td>718</td>
        </tr>
        """,
        market="KOSPI",
    )

    assert row["status"] == "ok"
    assert row["name"] == "KOSPI"
    assert row["as_of"] == "18:06"
    assert row["foreign_net_buy_100m_krw"] == -52967
    assert row["institution_net_buy_100m_krw"] == 12542
    assert row["foreign_institution_sum_100m_krw"] == -40425
    assert row["bias"] == "institution_buy"


def test_parse_naver_program_trading_html_extracts_latest_row() -> None:
    row = parse_naver_program_trading_html(
        """
        <tr>
          <th>시간</th><th>차익거래</th><th>비차익거래</th><th>전체</th>
        </tr>
        <tr>
          <td>18:05</td>
          <td>6,028</td><td>5,630</td><td>398</td>
          <td>95,574</td><td>129,187</td><td>-33,613</td>
          <td>101,602</td><td>134,817</td><td>-33,215</td>
        </tr>
        """,
        market="KOSPI",
    )

    assert row["status"] == "ok"
    assert row["market"] == "KOSPI"
    assert row["as_of"] == "18:05"
    assert row["arbitrage_net_buy_100m_krw"] == 398
    assert row["non_arbitrage_net_buy_100m_krw"] == -33613
    assert row["total_net_buy_100m_krw"] == -33215
    assert row["bias"] == "program_sell"


def test_parse_naver_fx_html_extracts_usd_krw() -> None:
    row = parse_naver_fx_html(
        """
        <li class="on">
          <a href="/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW" class="head usd">
            <h3 class="h_lst"><span class="blind">미국 USD</span></h3>
            <div class="head_info point_up">
              <span class="value">1,465.50</span>
              <span class="change">7.50</span>
              <span class="blind">상승</span>
            </div>
          </a>
          <div class="graph_info"><span class="time">2026.05.08 22:06</span><span class="source">하나은행 기준</span></div>
        </li>
        """
    )

    assert row["status"] == "ok"
    assert row["code"] == "USD/KRW"
    assert row["value"] == 1465.5
    assert row["change"] == 7.5
    assert row["direction"] == "up"


def test_build_futures_basis_compares_futures_to_kospi200() -> None:
    payload = build_futures_basis(
        [
            {"code": "KPI200", "value": 1151.17, "change_pct": 0.11, "status": "ok"},
            {"code": "FUT", "value": 1153.25, "change_pct": -0.47, "status": "ok"},
        ]
    )

    assert payload["status"] == "ok"
    assert payload["basis"] == 2.08
    assert payload["basis_signal"] == "contango"
    assert payload["futures_change_pct"] == -0.47


def test_market_pulse_caps_score_when_fx_program_and_flow_pressure_stack() -> None:
    components = MarketPulseService._score_components(
        indices=[
            {"status": "ok", "code": "KOSPI", "change_pct": 1.2},
            {"status": "ok", "code": "KOSDAQ", "change_pct": 1.1},
        ],
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[
            {"status": "ok", "market": "KOSPI", "foreign_institution_sum_100m_krw": -50_000},
            {"status": "ok", "market": "KOSDAQ", "foreign_institution_sum_100m_krw": -20_000},
            {"status": "ok", "market": "FUT", "foreign_net_buy_100m_krw": -10_000},
        ],
        program_trading=[
            {"status": "ok", "market": "KOSPI", "total_net_buy_100m_krw": -40_000},
        ],
        fx={"status": "ok", "change": 12.0},
        futures={"status": "ok", "basis": 2.0},
    )

    assert components["risk_cap"]["active"] is True
    assert components["risk_cap"]["cap"] == 65.0
    assert components["total_score"] <= 65.0


def test_market_pulse_caps_score_at_75_when_two_pressure_reasons_stack() -> None:
    components = MarketPulseService._score_components(
        indices=[
            {"status": "ok", "code": "KOSPI", "change_pct": 1.4},
            {"status": "ok", "code": "KOSDAQ", "change_pct": 1.3},
        ],
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[
            {"status": "ok", "market": "KOSPI", "foreign_institution_sum_100m_krw": -35_000},
            {"status": "ok", "market": "FUT", "foreign_net_buy_100m_krw": -5_000},
        ],
        program_trading=[
            {"status": "ok", "market": "KOSPI", "total_net_buy_100m_krw": -40_000},
        ],
        fx={"status": "ok", "change": 0.0},
        futures={"status": "ok", "basis": 2.0},
    )

    assert components["risk_cap"]["active"] is True
    assert components["risk_cap"]["cap"] == 75.0
    assert components["risk_cap"]["reasons"] == [
        "foreign_flow_pressure",
        "program_sell_pressure",
    ]
    assert components["total_score"] <= 75.0


def test_market_pulse_counts_index_dispersion_as_cap_pressure() -> None:
    components = MarketPulseService._score_components(
        indices=[
            {"status": "ok", "code": "KOSPI", "change_pct": 2.0},
            {"status": "ok", "code": "KOSDAQ", "change_pct": 0.2},
        ],
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[],
        program_trading=[
            {"status": "ok", "market": "KOSPI", "total_net_buy_100m_krw": -40_000},
        ],
        fx={"status": "ok", "change": 0.0},
        futures={"status": "ok", "basis": 0.0},
    )

    assert components["risk_cap"]["active"] is True
    assert components["risk_cap"]["cap"] == 75.0
    assert components["risk_cap"]["reasons"] == [
        "program_sell_pressure",
        "index_dispersion_high",
    ]
    assert components["total_score"] <= 75.0


def test_market_pulse_downgrades_clean_risk_on_under_active_cap() -> None:
    regime, _score, risk_flags = MarketPulseService._classify(
        indices=[
            {"status": "ok", "code": "KOSPI", "change_pct": 1.2},
            {"status": "ok", "code": "KOSDAQ", "change_pct": 1.1},
        ],
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[
            {"status": "ok", "market": "KOSPI", "foreign_institution_sum_100m_krw": -50_000},
            {"status": "ok", "market": "KOSDAQ", "foreign_institution_sum_100m_krw": -20_000},
            {"status": "ok", "market": "FUT", "foreign_net_buy_100m_krw": -10_000},
        ],
        program_trading=[
            {"status": "ok", "market": "KOSPI", "total_net_buy_100m_krw": -40_000},
        ],
        fx={"status": "ok", "change": 12.0},
        futures={"status": "ok", "basis": 2.0},
    )

    assert regime == "risk_on_with_pressure"
    assert "foreign_flow_pressure" in risk_flags
    assert "program_sell_pressure" in risk_flags
    assert "usd_krw_up_pressure" in risk_flags


def test_market_pulse_flags_core_index_derivative_divergence() -> None:
    indices = [
        {"status": "ok", "code": "KOSPI", "change_pct": 5.4},
        {"status": "ok", "code": "KOSDAQ", "change_pct": 5.8},
        {"status": "ok", "code": "KPI200", "change_pct": -5.9},
        {"status": "ok", "code": "FUT", "change_pct": -4.7},
    ]

    components = MarketPulseService._score_components(
        indices=indices,
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[],
        program_trading=[],
        fx={"status": "ok", "change": 0.0},
        futures={"status": "ok", "basis": 2.0},
    )
    regime, _score, risk_flags = MarketPulseService._classify(
        indices=indices,
        sectors={"items": [{"direction": "positive"} for _ in range(8)]},
        investor_flows=[],
        program_trading=[],
        fx={"status": "ok", "change": 0.0},
        futures={"status": "ok", "basis": 2.0},
    )
    gaps = MarketPulseService._data_gaps(
        indices=indices,
        sectors={"items": [{"direction": "positive"}]},
        investor_flows=[],
        program_trading=[],
        fx={"status": "ok"},
        futures={"status": "ok"},
    )

    assert "index_derivative_divergence" in components["risk_cap"]["reasons"]
    assert components["total_score"] <= 70.0
    assert regime == "risk_on_with_pressure"
    assert "index_derivative_divergence" in risk_flags
    assert "index_coherence_warning:core_vs_derivatives" in gaps


def test_market_pulse_builds_sector_and_block_alignment(tmp_path) -> None:
    service = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "pulse.db")),
        strategy_signal_provider=_FakeSignals(),
    )
    latest = {
        "status": "ok",
        "indices": [{"code": "KOSPI", "change_pct": 0.5, "status": "ok"}],
        "sectors": service._sector_summary(),
    }
    context = service.context_for_blocks(
        blocks=[
            {
                "block_id": "blk_005930",
                "symbol": "005930",
                "name": "삼성전자",
            }
        ],
        quotes=[
            {
                "symbol": "005930",
                "name": "삼성전자",
                "raw": {
                    "bstp_kor_isnm": "반도체",
                    "rprs_mrkt_kor_name": "KOSPI",
                },
            }
        ],
    )
    assert context["status"] == "missing"

    saved = service.repository.save_snapshot(
        {
            **latest,
            "captured_at": "2026-05-10T00:00:00+00:00",
            "trading_day": "2026-05-10",
            "regime": "risk_on",
            "score": 70,
            "block_alignment": [],
            "risk_flags": [],
            "data_gaps": [],
        }
    )
    assert saved["id"] == 1

    context = service.context_for_blocks(
        blocks=[
            {
                "block_id": "blk_005930",
                "symbol": "005930",
                "name": "삼성전자",
            }
        ],
        quotes=[
            {
                "symbol": "005930",
                "name": "삼성전자",
                "raw": {
                    "bstp_kor_isnm": "반도체",
                    "rprs_mrkt_kor_name": "KOSPI",
                },
            }
        ],
    )

    assert context["regime"] == "risk_on"
    assert context["block_alignment"][0]["sector_alignment"] == "strong"
    assert context["block_alignment"][0]["market_alignment"] == "positive"


def test_market_pulse_collect_saves_snapshot_with_mocked_indices(monkeypatch, tmp_path) -> None:
    service = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "pulse.db")),
        strategy_signal_provider=_FakeSignals(),
    )

    async def fake_collect_indices() -> list[dict]:
        return [
            {
                "code": "KOSPI",
                "name": "KOSPI",
                "value": 7498.0,
                "change_pct": 0.8,
                "status": "ok",
            },
            {
                "code": "KOSDAQ",
                "name": "KOSDAQ",
                "value": 1207.0,
                "change_pct": 0.7,
                "status": "ok",
            },
        ]

    async def fake_collect_investor_flows() -> list[dict]:
        return [
            {
                "market": "KOSPI",
                "status": "ok",
                "foreign_net_buy_100m_krw": 12000,
                "institution_net_buy_100m_krw": 9000,
                "foreign_institution_sum_100m_krw": 21000,
            },
            {
                "market": "KOSDAQ",
                "status": "ok",
                "foreign_net_buy_100m_krw": 4000,
                "institution_net_buy_100m_krw": 2000,
                "foreign_institution_sum_100m_krw": 6000,
            },
        ]

    async def fake_collect_program_trading() -> list[dict]:
        return [
            {
                "market": "KOSPI",
                "status": "ok",
                "total_net_buy_100m_krw": -10000,
                "non_arbitrage_net_buy_100m_krw": -9000,
            }
        ]

    async def fake_collect_fx_snapshot() -> dict:
        return {"status": "ok", "code": "USD/KRW", "value": 1465.5, "change": 7.5}

    monkeypatch.setattr(service, "_collect_indices", fake_collect_indices)
    monkeypatch.setattr(service, "_collect_investor_flows", fake_collect_investor_flows)
    monkeypatch.setattr(service, "_collect_program_trading", fake_collect_program_trading)
    monkeypatch.setattr(service, "_collect_fx_snapshot", fake_collect_fx_snapshot)
    payload = asyncio.run(service.collect(clock={"date": "2026-05-10"}))

    assert payload["status"] == "ok"
    assert payload["regime"] == "risk_on"
    assert payload["score"] > 60
    assert payload["score_method_version"] == "v3"
    assert set(payload["score_components"]) >= {
        "index_score",
        "sector_score",
        "investor_flow_score",
        "program_score",
        "fx_risk_score",
        "futures_basis_score",
        "block_exposure_score",
        "total_score",
        "risk_cap",
    }
    assert payload["score"] == payload["score_components"]["total_score"]
    for key, component in payload["score_components"].items():
        if key == "total_score":
            assert isinstance(component, (int, float))
            continue
        if key == "risk_cap":
            assert component["active"] is False
            assert component["cap"] == 100.0
            assert component["reasons"] == []
            continue
        assert isinstance(component["score"], (int, float))
        assert component["label"]
        assert component["reason"]
    assert payload["investor_flows"][0]["market"] == "KOSPI"
    assert payload["program_trading"][0]["market"] == "KOSPI"
    assert payload["fx"]["code"] == "USD/KRW"
    assert payload["futures"]["status"] == "missing"
    assert "indices" in payload
    assert "sectors" in payload
    assert "block_alignment" in payload
    assert "risk_flags" in payload
    assert service.latest()["id"] == payload["id"]


def test_market_pulse_block_exposure_flags_sector_concentration(monkeypatch, tmp_path) -> None:
    service = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "pulse.db")),
        strategy_signal_provider=_FakeSignals(),
    )

    async def fake_collect_indices() -> list[dict]:
        return [{"code": "KOSPI", "name": "KOSPI", "value": 2500.0, "change_pct": 0.1, "status": "ok"}]

    async def fake_collect_investor_flows() -> list[dict]:
        return []

    async def fake_collect_program_trading() -> list[dict]:
        return []

    async def fake_collect_fx_snapshot() -> dict:
        return {"status": "disabled"}

    monkeypatch.setattr(service, "_collect_indices", fake_collect_indices)
    monkeypatch.setattr(service, "_collect_investor_flows", fake_collect_investor_flows)
    monkeypatch.setattr(service, "_collect_program_trading", fake_collect_program_trading)
    monkeypatch.setattr(service, "_collect_fx_snapshot", fake_collect_fx_snapshot)

    payload = asyncio.run(
        service.collect(
            clock={"date": "2026-05-10"},
            blocks=[
                {"block_id": "blk_005930", "symbol": "005930", "name": "삼성전자"},
                {"block_id": "blk_000660", "symbol": "000660", "name": "SK하이닉스"},
                {"block_id": "blk_042700", "symbol": "042700", "name": "한미반도체"},
                {"block_id": "blk_035420", "symbol": "035420", "name": "NAVER"},
            ],
            quotes=[
                {"symbol": "005930", "raw": {"bstp_kor_isnm": "반도체", "rprs_mrkt_kor_name": "KOSPI"}},
                {"symbol": "000660", "raw": {"bstp_kor_isnm": "반도체", "rprs_mrkt_kor_name": "KOSPI"}},
                {"symbol": "042700", "raw": {"bstp_kor_isnm": "반도체", "rprs_mrkt_kor_name": "KOSPI"}},
                {"symbol": "035420", "raw": {"bstp_kor_isnm": "인터넷", "rprs_mrkt_kor_name": "KOSPI"}},
            ],
        )
    )

    exposure = payload["block_exposure"]
    assert exposure["status"] == "caution"
    assert exposure["block_count"] == 4
    assert exposure["sector_weights"]["반도체"] == pytest.approx(0.75)
    assert exposure["market_weights"]["KOSPI"] == pytest.approx(1.0)
    assert "sector_concentration:반도체" in exposure["concentration_flags"]
    assert payload["score_components"]["block_exposure_score"]["score"] == 0.0


def test_context_for_blocks_recomputes_block_exposure_from_supplied_blocks_and_quotes(tmp_path) -> None:
    service = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "pulse.db")),
        strategy_signal_provider=_FakeSignals(),
    )
    saved = service.repository.save_snapshot(
        {
            "status": "ok",
            "captured_at": "2026-05-10T00:00:00+00:00",
            "trading_day": "2026-05-10",
            "regime": "risk_on",
            "score": 70,
            "indices": [{"code": "KOSPI", "change_pct": 0.5, "status": "ok"}],
            "sectors": service._sector_summary(),
            "block_alignment": [],
            "block_exposure": {
                "status": "ok",
                "block_count": 0,
                "sector_weights": {},
                "market_weights": {},
                "concentration_flags": [],
                "pressure_flags": [],
            },
            "risk_flags": [],
            "data_gaps": [],
        }
    )
    assert saved["id"] == 1

    context = service.context_for_blocks(
        blocks=[
            {"block_id": "blk_005930", "symbol": "005930", "name": "삼성전자"},
            {"block_id": "blk_035420", "symbol": "035420", "name": "NAVER"},
        ],
        quotes={
            "005930": {"raw": {"bstp_kor_isnm": "반도체", "rprs_mrkt_kor_name": "KOSPI"}},
            "035420": {"raw": {"bstp_kor_isnm": "인터넷", "rprs_mrkt_kor_name": "KOSPI"}},
        },
    )

    assert context["block_exposure"]["block_count"] == 2
    assert context["block_exposure"]["sector_weights"] == {
        "반도체": pytest.approx(0.5),
        "인터넷": pytest.approx(0.5),
    }
    assert context["block_exposure"]["market_weights"]["KOSPI"] == pytest.approx(1.0)


def test_context_for_blocks_accepts_extra_provider_kwargs(tmp_path) -> None:
    service = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "pulse.db")),
        strategy_signal_provider=_FakeSignals(),
    )
    service.repository.save_snapshot(
        {
            "status": "ok",
            "captured_at": "2026-05-10T00:00:00+00:00",
            "trading_day": "2026-05-10",
            "regime": "choppy",
            "score": 50,
            "indices": [],
            "sectors": {"status": "ok", "items": []},
            "block_alignment": [],
            "block_exposure": {},
            "risk_flags": [],
            "data_gaps": [],
        }
    )

    context = service.context_for_blocks(blocks=[], quotes=[], account={}, symbols=[])

    assert context["status"] == "ok"
    assert context["block_exposure"]["block_count"] == 0


def test_block_alignment_keeps_missing_market_unknown(tmp_path) -> None:
    service = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "pulse.db")),
        strategy_signal_provider=_FakeSignals(),
    )
    service.repository.save_snapshot(
        {
            "status": "ok",
            "captured_at": "2026-05-10T00:00:00+00:00",
            "trading_day": "2026-05-10",
            "regime": "risk_on",
            "score": 70,
            "indices": [{"code": "KOSPI", "change_pct": 1.0, "status": "ok"}],
            "sectors": service._sector_summary(),
            "block_alignment": [],
            "risk_flags": [],
            "data_gaps": [],
        }
    )

    context = service.context_for_blocks(
        blocks=[{"block_id": "blk_005930", "symbol": "005930", "name": "삼성전자"}],
        quotes=[{"symbol": "005930", "raw": {"bstp_kor_isnm": "반도체"}}],
    )

    assert context["block_alignment"][0]["market"] == ""
    assert context["block_alignment"][0]["market_alignment"] == "unknown"


def test_market_pulse_averages_only_available_core_indices() -> None:
    indices = [{"code": "KOSPI", "change_pct": 1.0, "status": "ok"}]
    sectors = {"status": "ok", "items": []}

    components = MarketPulseService._score_components(indices=indices, sectors=sectors)
    regime, score, risk_flags = MarketPulseService._classify(indices=indices, sectors=sectors)

    assert components["index_score"]["score"] == pytest.approx(18.0)
    assert regime == "risk_on"
    assert score == pytest.approx(68.0)
    assert risk_flags == []


def test_classify_does_not_flag_dispersion_with_one_core_index() -> None:
    indices = [{"code": "KOSPI", "change_pct": 1.6, "status": "ok"}]
    sectors = {"status": "ok", "items": []}

    regime, score, risk_flags = MarketPulseService._classify(indices=indices, sectors=sectors)

    assert regime == "risk_on"
    assert score == pytest.approx(78.8)
    assert "index_dispersion_high" not in risk_flags


def test_score_component_reason_only_lists_available_core_indices() -> None:
    components = MarketPulseService._score_components(
        indices=[{"code": "KOSPI", "change_pct": 1.6, "status": "ok"}],
        sectors={"status": "ok", "items": []},
    )

    reason = components["index_score"]["reason"]
    assert reason == "KOSPI 1.60%"
    assert "KOSDAQ 0.00%" not in reason
