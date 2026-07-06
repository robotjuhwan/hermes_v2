from __future__ import annotations

import asyncio
import base64
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import tradecraft.services.etf_research as etf_research
from tradecraft.services.etf_research import (
    ConfiguredETFResearchProvider,
    ETFMarketSnapshot,
    ETFResearchRepository,
    collect_etf_research,
    fetch_naver_etf_universe,
    merge_etf_universe,
    parse_naver_etf_item_list,
    parse_etf_universe_config,
    score_etf_snapshot,
    stale_etf_symbols,
)


def _decode_gzip_base64(value: str) -> str:
    assert value.startswith("gzip+base64:")
    return gzip.decompress(base64.b64decode(value.removeprefix("gzip+base64:"))).decode(
        "utf-8"
    )


def test_parse_etf_universe_config_accepts_default_symbols() -> None:
    items = parse_etf_universe_config("069500:KODEX 200, 102110:TIGER 200, bad:skip")

    assert [item.symbol for item in items] == ["069500", "102110"]
    assert [item.name for item in items] == ["KODEX 200", "TIGER 200"]
    assert all(item.category == "core" for item in items)


def test_parse_etf_universe_config_uses_symbol_as_name_when_omitted() -> None:
    items = parse_etf_universe_config("069500, 12345:skip, A69500:skip")

    assert len(items) == 1
    assert items[0].symbol == "069500"
    assert items[0].name == "069500"


def test_etf_universe_merges_configured_and_symbol_directory_rows() -> None:
    configured = parse_etf_universe_config("069500:KODEX 200")
    directory_rows = [
        {"symbol": "091160", "company_name": "KODEX 반도체", "market": "ETF"},
        {"symbol": "102110", "company_name": "TIGER 200", "market": "ETF"},
    ]

    merged = merge_etf_universe(
        configured=configured,
        symbol_directory_rows=directory_rows,
    )

    assert [row.symbol for row in merged] == ["069500", "091160", "102110"]
    assert [row.category for row in merged] == ["core", "expanded", "expanded"]


def test_etf_universe_dedupes_and_accepts_brand_prefix_rows() -> None:
    configured = parse_etf_universe_config("069500:KODEX 200,102110:TIGER 200")
    directory_rows = [
        {"symbol": "102110", "company_name": "TIGER 200 duplicate", "market": ""},
        {"symbol": "305720", "company_name": "KODEX 2차전지산업", "market": ""},
        {"symbol": "385560", "company_name": "KBSTAR KIS국고채30년", "market": ""},
        {"symbol": "483340", "company_name": "RISE 미국30년국채", "market": ""},
        {"symbol": "456880", "company_name": "ACE 미국빅테크", "market": ""},
        {"symbol": "475070", "company_name": "KOSEF 금융채", "market": ""},
        {"symbol": "A12345", "company_name": "KODEX invalid", "market": "ETF"},
        {"symbol": "12345", "company_name": "TIGER invalid", "market": "ETF"},
    ]

    merged = merge_etf_universe(
        configured=configured,
        symbol_directory_rows=directory_rows,
    )

    assert [row.symbol for row in merged] == [
        "069500",
        "102110",
        "305720",
        "385560",
        "483340",
        "456880",
    ]
    assert merged[1].name == "TIGER 200"


def test_parse_naver_etf_item_list_adds_market_etf_rows() -> None:
    raw = (
        '{"resultCode":"success","result":{"etfItemList":['
        '{"itemcode":"069500","itemname":"KODEX 200","etfTabCode":1},'
        '{"itemcode":"360750","itemname":"TIGER 미국S&P500","etfTabCode":4},'
        '{"itemcode":"bad","itemname":"BROKEN","etfTabCode":1}'
        "]}}"
    ).encode("cp949")

    rows = parse_naver_etf_item_list(raw)

    assert [row.symbol for row in rows] == ["069500", "360750"]
    assert [row.name for row in rows] == ["KODEX 200", "TIGER 미국S&P500"]
    assert all(row.category == "naver_etf" for row in rows)
    assert rows[1].tags == ["naver_etf", "tab_4"]


def test_fetch_naver_etf_universe_backs_off_after_network_failure(
    monkeypatch,
) -> None:
    calls = 0

    def fail_urlopen(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise OSError("socket exhausted")

    monkeypatch.setattr(etf_research, "urlopen", fail_urlopen)
    monkeypatch.setattr(etf_research, "_NAVER_ETF_UNIVERSE_CACHE", [])
    monkeypatch.setattr(etf_research, "_NAVER_ETF_UNIVERSE_CACHE_AT", None)
    monkeypatch.setattr(etf_research, "_NAVER_ETF_UNIVERSE_FAILURE_UNTIL", None)

    with pytest.raises(OSError):
        fetch_naver_etf_universe(limit=10, timeout_sec=1)
    with pytest.raises(RuntimeError, match="backoff"):
        fetch_naver_etf_universe(limit=10, timeout_sec=1)

    assert calls == 1


def test_repository_stores_and_lists_universe_rows(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    items = parse_etf_universe_config("069500:KODEX 200,102110:TIGER 200")

    repo.upsert_universe(items)

    rows = repo.list_universe()
    assert [row["symbol"] for row in rows] == ["069500", "102110"]
    assert rows[0]["name"] == "KODEX 200"
    assert rows[0]["category"] == "core"
    assert rows[0]["tags"] == []


def test_latest_snapshot_before_collection_returns_missing(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))

    assert repo.latest_snapshot("069500") == {"status": "missing", "symbol": "069500"}


def test_save_ok_snapshot_and_score_then_read_latest(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    captured_at = datetime(2026, 5, 14, 1, 2, 3, tzinfo=timezone.utc).isoformat()
    snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=42150.0,
        change_pct=1.8,
        volume=120_000,
        turnover_krw=5_058_000_000.0,
        source="test",
        raw={"market": "krx"},
        captured_at=captured_at,
        status="ok",
        error_message="",
    )
    score = score_etf_snapshot(snapshot)

    repo.save_snapshot(snapshot)
    repo.save_score(score)

    latest_snapshot = repo.latest_snapshot("069500")
    latest_score = repo.latest_score("069500")
    assert latest_snapshot["symbol"] == "069500"
    assert latest_snapshot["status"] == "ok"
    assert latest_snapshot["raw"] == {"market": "krx"}
    assert latest_score["symbol"] == "069500"
    assert latest_score["label"] in {"core_fit", "theme_momentum"}
    assert latest_score["liquidity_score"] > 0
    assert "ETF" in " ".join(latest_score["reasons"])


def test_save_snapshot_compacts_large_raw_payload(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=42150.0,
        change_pct=1.8,
        volume=120_000,
        turnover_krw=5_058_000_000.0,
        source="test",
        raw={
            "stck_prpr": "42150",
            "prdy_ctrt": "1.80",
            "acml_vol": "120000",
            "acml_tr_pbmn": "5058000000",
            "hts_kor_isnm": "KODEX 200",
            "large_unused_blob": "x" * 5000,
        },
        captured_at=datetime(2026, 5, 14, 1, 2, 3, tzinfo=timezone.utc).isoformat(),
        status="ok",
        error_message="",
    )

    repo.save_snapshot(snapshot)

    latest_snapshot = repo.latest_snapshot("069500")
    raw = latest_snapshot["raw"]
    assert raw["stck_prpr"] == "42150"
    assert raw["acml_tr_pbmn"] == "5058000000"
    assert raw["_raw_compacted"] is True
    assert raw["_raw_key_count"] == 6
    assert "large_unused_blob" not in raw


def test_repository_normalizes_timestamps_for_latest_ordering(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    older_snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=42000.0,
        change_pct=0.5,
        volume=20_000,
        turnover_krw=840_000_000.0,
        source="test",
        raw={"sequence": "older"},
        captured_at="2026-05-14T10:00:00+09:00",
        status="ok",
        error_message="",
    )
    newer_snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=43000.0,
        change_pct=1.2,
        volume=30_000,
        turnover_krw=1_290_000_000.0,
        source="test",
        raw={"sequence": "newer"},
        captured_at="2026-05-14T02:00:00Z",
        status="ok",
        error_message="",
    )
    older_score = score_etf_snapshot(older_snapshot)
    older_score.scored_at = "2026-05-14T10:00:00+09:00"
    newer_score = score_etf_snapshot(newer_snapshot)
    newer_score.scored_at = "2026-05-14T02:00:00Z"

    repo.save_snapshot(older_snapshot)
    repo.save_snapshot(newer_snapshot)
    repo.save_score(older_score)
    repo.save_score(newer_score)

    latest_snapshot = repo.latest_snapshot("069500")
    latest_score = repo.latest_score("069500")
    assert latest_snapshot["raw"] == {"sequence": "newer"}
    assert latest_snapshot["captured_at"] == "2026-05-14T02:00:00+00:00"
    assert latest_score["scored_at"] == "2026-05-14T02:00:00+00:00"


def test_error_snapshot_scores_unknown_with_risk_reason(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    snapshot = ETFMarketSnapshot(
        symbol="102110",
        name="TIGER 200",
        price=0.0,
        change_pct=0.0,
        volume=0,
        turnover_krw=0.0,
        source="test",
        raw={"error": "timeout"},
        captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
        status="error",
        error_message="quote timeout",
    )

    score = score_etf_snapshot(snapshot)
    repo.save_snapshot(snapshot)
    repo.save_score(score)

    latest_score = repo.latest_score("102110")
    assert latest_score["label"] == "unknown"
    assert latest_score["risk_score"] > 0
    assert any("quote timeout" in risk for risk in latest_score["risks"])


def test_error_only_etf_research_is_not_usable(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    repo.upsert_universe(parse_etf_universe_config("102110:TIGER 200"))
    snapshot = ETFMarketSnapshot(
        symbol="102110",
        name="TIGER 200",
        price=0.0,
        change_pct=0.0,
        volume=0,
        turnover_krw=0.0,
        source="test",
        raw={"error": "timeout"},
        captured_at=datetime(2026, 5, 14, tzinfo=timezone.utc).isoformat(),
        status="error",
        error_message="quote timeout",
    )
    repo.save_snapshot(snapshot)
    repo.save_score(score_etf_snapshot(snapshot))
    provider = ConfiguredETFResearchProvider(
        repository_factory=lambda: repo,
        universe_provider=lambda: parse_etf_universe_config("102110:TIGER 200"),
    )

    status = provider.status()

    assert status["status"] == "waiting"
    assert status["usable_research_count"] == 0


def test_stale_etf_symbols_can_rotate_large_universe(tmp_path: Path) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    universe = parse_etf_universe_config(
        ",".join(f"{69000 + index:06d}:ETF {index}" for index in range(10))
    )

    first = stale_etf_symbols(
        repo,
        universe,
        stale_sec=1800,
        max_symbols=3,
        rotation_key="2026-06-01",
    )
    second = stale_etf_symbols(
        repo,
        universe,
        stale_sec=1800,
        max_symbols=3,
        rotation_key="2026-06-02",
    )

    assert len(first) == 3
    assert len(second) == 3
    assert first != second
    assert set(first).issubset({item.symbol for item in universe})
    assert set(second).issubset({item.symbol for item in universe})


def test_etf_research_repository_prunes_old_snapshots_and_scores(
    tmp_path: Path,
) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()

    old_snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=100.0,
        change_pct=0.0,
        volume=1,
        turnover_krw=100.0,
        source="test",
        raw={"source": "old_etf_snapshot"},
        captured_at=old,
        status="ok",
    )
    fresh_snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=101.0,
        change_pct=1.0,
        volume=2,
        turnover_krw=202.0,
        source="test",
        raw={},
        captured_at=fresh,
        status="ok",
    )
    old_score = score_etf_snapshot(old_snapshot)
    old_score.scored_at = old
    fresh_score = score_etf_snapshot(fresh_snapshot)
    fresh_score.scored_at = fresh
    repo.save_snapshot(old_snapshot)
    repo.save_score(old_score)
    repo.save_snapshot(fresh_snapshot)
    repo.save_score(fresh_score)

    result = repo.prune_history(retention_days=30)

    assert result["status"] == "ok"
    assert result["snapshots_deleted"] == 1
    assert result["scores_deleted"] == 1
    assert result["archived"]["etf_market_snapshots"] == 1
    assert result["vacuumed"] is True
    assert repo.status()["snapshot_count"] == 1
    assert repo.status()["score_count"] == 1
    with sqlite3.connect(repo.db_path) as conn:
        archived_raw = conn.execute(
            "SELECT raw_json FROM etf_market_snapshots_archive"
        ).fetchone()[0]
    assert json.loads(_decode_gzip_base64(archived_raw)) == {
        "source": "old_etf_snapshot"
    }


def test_etf_research_repository_tiered_retention_deletes_cold_and_archives_warm(
    tmp_path: Path,
) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    now = datetime.now(timezone.utc)
    cold = (now - timedelta(days=20)).isoformat()
    warm = (now - timedelta(days=10)).isoformat()
    fresh = now.isoformat()

    for symbol, captured_at, raw in [
        ("069500", cold, {"tier": "cold"}),
        ("102110", warm, {"tier": "warm"}),
        ("091160", fresh, {"tier": "fresh"}),
    ]:
        snapshot = ETFMarketSnapshot(
            symbol=symbol,
            name=symbol,
            price=100.0,
            change_pct=0.0,
            volume=1,
            turnover_krw=100.0,
            source="test",
            raw=raw,
            captured_at=captured_at,
            status="ok",
        )
        score = score_etf_snapshot(snapshot)
        score.scored_at = captured_at
        repo.save_snapshot(snapshot)
        repo.save_score(score)

    result = repo.prune_history(retention_days=7, archive_retention_days=14)

    assert result["status"] == "ok"
    assert result["cold_retention"]["tables"]["etf_market_snapshots"]["deleted"] == 1
    assert result["archived"]["etf_market_snapshots"] == 1
    assert result["scores_deleted"] == 2
    assert result["vacuumed"] is True
    assert repo.status()["snapshot_count"] == 1
    assert repo.status()["score_count"] == 1
    with sqlite3.connect(repo.db_path) as conn:
        active_symbols = {
            row[0]
            for row in conn.execute("SELECT symbol FROM etf_market_snapshots")
        }
        archived = conn.execute(
            "SELECT symbol, raw_json FROM etf_market_snapshots_archive"
        ).fetchall()
    assert active_symbols == {"091160"}
    assert [(row[0], json.loads(_decode_gzip_base64(row[1]))) for row in archived] == [
        ("102110", {"tier": "warm"})
    ]


def test_collect_etf_research_prunes_history_after_collection(
    tmp_path: Path,
) -> None:
    repo = ETFResearchRepository(str(tmp_path / "etf_research.db"))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    old_snapshot = ETFMarketSnapshot(
        symbol="069500",
        name="KODEX 200",
        price=100.0,
        change_pct=0.0,
        volume=1,
        turnover_krw=100.0,
        source="test",
        raw={},
        captured_at=old,
        status="ok",
    )
    old_score = score_etf_snapshot(old_snapshot)
    old_score.scored_at = old
    repo.save_snapshot(old_snapshot)
    repo.save_score(old_score)

    async def fetch_quote(symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "name": "KODEX 200",
            "price": 101.0,
            "raw": {
                "stck_prpr": "101",
                "prdy_ctrt": "1.0",
                "acml_vol": "2",
                "acml_tr_pbmn": "202",
            },
        }

    result = asyncio.run(
        collect_etf_research(
            repository=repo,
            configured=parse_etf_universe_config("069500:KODEX 200"),
            fetch_quote=fetch_quote,
            symbols=["069500"],
            retention_days=30,
        )
    )

    assert result["status"] == "ok"
    assert result["retention"]["snapshots_deleted"] == 1
    assert result["retention"]["scores_deleted"] == 1
    assert result["retention"]["vacuumed"] is True
    assert repo.status()["snapshot_count"] == 1
