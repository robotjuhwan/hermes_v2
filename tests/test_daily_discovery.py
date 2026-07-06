from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from tradecraft.services.daily_discovery import (
    DailyDiscoveryConfig,
    DailyDiscoveryRepository,
    DailyDiscoveryService,
    _compact_discovery_result,
)
from tradecraft.services.naver_reports import NaverReportRepository


class _DirectorySource:
    def __init__(self, rows_per_market: int = 20) -> None:
        self.rows = {
            "KOSPI": [
                {"symbol": f"10{idx:04d}", "name": f"피코스피{idx}", "market": "KOSPI"}
                for idx in range(rows_per_market)
            ],
            "KOSDAQ": [
                {"symbol": f"20{idx:04d}", "name": f"피코스닥{idx}", "market": "KOSDAQ"}
                for idx in range(rows_per_market)
            ],
        }

    def list_symbol_directory(
        self,
        *,
        market: str = "",
        limit: int = 100,
        exclude_symbols: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        excluded = set(exclude_symbols or set())
        return [
            row
            for row in self.rows.get(str(market).upper(), [])
            if row["symbol"] not in excluded
        ][:limit]


class _SymbolAnalysis:
    def __init__(self, fail_symbol: str = "") -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.fail_symbol = fail_symbol

    async def run(
        self,
        symbol_or_name: str,
        *,
        trigger: str = "user_request",
        force_collect: bool = True,
    ) -> dict[str, Any]:
        self.calls.append((symbol_or_name, trigger, force_collect))
        if symbol_or_name == self.fail_symbol:
            raise RuntimeError("analysis exploded")
        stance = "block_candidate" if symbol_or_name.endswith("3") else "watch"
        confidence = 0.82 if stance == "block_candidate" else 0.55
        return {
            "status": "ok",
            "symbol": symbol_or_name,
            "name": f"종목{symbol_or_name}",
            "analysis": {
                "id": len(self.calls),
                "symbol": symbol_or_name,
                "name": f"종목{symbol_or_name}",
                "stance": stance,
                "confidence": confidence,
                "summary": f"{symbol_or_name} deep study",
                "reasons": ["밸류와 수급 확인"],
                "risks": ["거래대금 확인 필요"],
            },
        }


class _FallbackOnlyDirectorySource:
    def __init__(self, rows: int = 30) -> None:
        self.rows = [
            {"symbol": f"30{idx:04d}", "name": f"폴백종목{idx}", "market": "KRX_LOOKUP"}
            for idx in range(rows)
        ]

    def list_symbol_directory(
        self,
        *,
        market: str = "",
        limit: int = 100,
        exclude_symbols: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if market:
            return []
        excluded = set(exclude_symbols or set())
        return [row for row in self.rows if row["symbol"] not in excluded][:limit]


def test_symbol_directory_lists_market_symbols_for_discovery(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "source": "test"},
            {"symbol": "000660", "name": "SK하이닉스", "market": "KOSPI", "source": "test"},
            {"symbol": "035720", "name": "카카오", "market": "KOSPI", "source": "test"},
            {"symbol": "091990", "name": "셀트리온헬스케어", "market": "KOSDAQ", "source": "test"},
            {"symbol": "277810", "name": "레인보우로보틱스", "market": "KOSDAQ", "source": "test"},
            {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "test"},
        ]
    )

    kospi = repo.list_symbol_directory(market="KOSPI", limit=10)
    kosdaq = repo.list_symbol_directory(market="KOSDAQ", limit=10)

    assert [row["symbol"] for row in kospi] == ["000660", "005930", "035720"]
    assert [row["symbol"] for row in kosdaq] == ["091990", "277810"]
    assert all(row["asset_class"] == "stock" for row in kospi + kosdaq)


def test_symbol_directory_listing_excludes_symbols_and_non_stock_assets(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "source": "test"},
            {"symbol": "000660", "name": "SK하이닉스", "market": "KOSPI", "source": "test"},
            {"symbol": "069500", "name": "KODEX 200", "market": "ETF", "source": "test"},
        ]
    )

    rows = repo.list_symbol_directory(
        market="KOSPI",
        limit=10,
        exclude_symbols={"005930"},
    )

    assert [row["symbol"] for row in rows] == ["000660"]


def test_symbol_directory_listing_excludes_bad_status_when_column_exists(tmp_path: Path) -> None:
    repo = NaverReportRepository(str(tmp_path / "reports.db"))
    repo.seed_symbol_directory(
        [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "source": "test"},
            {
                "symbol": "000660",
                "name": "SK하이닉스",
                "market": "KOSPI",
                "source": "test",
                "status": "halted",
            },
        ]
    )

    rows = repo.list_symbol_directory(market="KOSPI", limit=10)

    assert [row["symbol"] for row in rows] == ["005930"]


def test_daily_discovery_samples_five_kospi_and_five_kosdaq_deterministically(
    tmp_path: Path,
) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=None,
    )

    first = service.select_symbols(trading_day=date(2026, 5, 20))
    second = service.select_symbols(trading_day=date(2026, 5, 20))

    assert first == second
    assert len([row for row in first if row["market"] == "KOSPI"]) == 5
    assert len([row for row in first if row["market"] == "KOSDAQ"]) == 5
    assert len({row["symbol"] for row in first}) == 10


def test_daily_discovery_falls_back_to_full_directory_when_market_labels_missing(
    tmp_path: Path,
) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_FallbackOnlyDirectorySource(),
        symbol_analysis=None,
    )

    selected = service.select_symbols(trading_day=date(2026, 5, 20))

    assert len(selected) == 10
    assert len({row["symbol"] for row in selected}) == 10
    assert len([row for row in selected if row["market"] == "KOSPI"]) == 5
    assert len([row for row in selected if row["market"] == "KOSDAQ"]) == 5
    assert all(row["selection_market_fallback"] is True for row in selected)
    assert {row["source_market"] for row in selected} == {"KRX_LOOKUP"}


def test_daily_discovery_skips_recently_analyzed_symbols(tmp_path: Path) -> None:
    repo = DailyDiscoveryRepository(str(tmp_path / "discovery.db"))
    repo.save_run(
        {
            "trading_day": "2026-05-19",
            "status": "ok",
            "selected_symbols": ["100000", "200000"],
            "results": [
                {"symbol": "100000", "market": "KOSPI", "status": "ok"},
                {"symbol": "200000", "market": "KOSDAQ", "status": "ok"},
            ],
        }
    )
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(
            db_path=str(tmp_path / "discovery.db"),
            exclude_recent_days=5,
        ),
        directory_source=_DirectorySource(),
        symbol_analysis=None,
    )

    selected = service.select_symbols(trading_day=date(2026, 5, 20))
    symbols = {row["symbol"] for row in selected}

    assert "100000" not in symbols
    assert "200000" not in symbols


def test_daily_discovery_runs_deep_analysis_for_every_selected_symbol(tmp_path: Path) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=analyzer,
    )

    result = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=True))

    assert result["status"] == "ok"
    assert result["analyzed_count"] == 10
    assert len(analyzer.calls) == 10
    assert all(call[1] == "daily_random_deep_research" for call in analyzer.calls)
    assert all(call[2] is True for call in analyzer.calls)
    assert result["summary"]["block_candidate_count"] >= 1


def test_daily_discovery_is_idempotent_for_same_trading_day(tmp_path: Path) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=analyzer,
    )

    first = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=False))
    second = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=False))

    assert first["status"] == "ok"
    assert second["status"] == "skipped"
    assert second["reason"] == "already_completed"
    assert len(analyzer.calls) == 10


def test_daily_discovery_reports_due_when_no_run_exists_for_open_day(
    tmp_path: Path,
) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(
            db_path=str(tmp_path / "discovery.db"),
            enabled=True,
        ),
        directory_source=_DirectorySource(),
        symbol_analysis=_SymbolAnalysis(),
    )

    assert service.should_run_for_day("2026-05-22") is True


def test_daily_discovery_not_due_after_completed_run(tmp_path: Path) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(
            db_path=str(tmp_path / "discovery.db"),
            enabled=True,
        ),
        directory_source=_DirectorySource(),
        symbol_analysis=_SymbolAnalysis(),
    )
    service.repository.save_run(
        {
            "trading_day": "2026-05-22",
            "status": "ok",
            "selected_symbols": ["100001"],
            "results": [{"symbol": "100001", "market": "KOSPI", "status": "ok"}],
        }
    )

    assert service.should_run_for_day(date(2026, 5, 22)) is False


def test_daily_discovery_not_due_when_disabled(tmp_path: Path) -> None:
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(
            db_path=str(tmp_path / "discovery.db"),
            enabled=False,
        ),
        directory_source=_DirectorySource(),
        symbol_analysis=_SymbolAnalysis(),
    )

    assert service.should_run_for_day("2026-05-22") is False


def test_daily_discovery_latest_context_is_compact(tmp_path: Path) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=analyzer,
    )
    asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=True))

    context = service.latest_context(limit=3)

    assert context["status"] == "ok"
    assert context["trading_day"] == "2026-05-20"
    assert len(context["items"]) == 3
    assert "block_candidate_count" in context["summary"]
    assert all("prompt" not in item["analysis"] for item in context["items"])
    assert context["block_candidates"]
    assert context["pre_surge_candidates"]


def test_daily_discovery_persists_pre_surge_signals_for_wiki_consumers(
    tmp_path: Path,
) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(),
        symbol_analysis=analyzer,
    )

    asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=True))
    run = service.repository.latest_run()
    candidates = [
        row
        for row in run["results"]
        if (row.get("pre_surge") or {}).get("is_candidate")
    ]

    assert candidates
    assert candidates[0]["pre_surge"]["lane"] == "pre_surge"
    assert candidates[0]["pre_surge"]["entry_bias"] == "scout_or_waiting_block"


def test_daily_discovery_marks_pre_surge_candidates_for_early_blocks() -> None:
    compact = _compact_discovery_result(
        {
            "symbol": "123450",
            "name": "선행후보",
            "market": "KOSDAQ",
            "status": "ok",
            "score": 84.0,
            "analysis": {
                "stance": "watch",
                "confidence": 0.72,
                "summary": "저PER, 저PBR, 52주 저점권에서 거래대금이 붙기 시작한 선행 매수 후보",
                "reasons": ["밸류 저평가", "눌림 이후 지지 확인", "섹터 순환매 가능성"],
                "risks": ["거래대금 확인 필요"],
            },
        }
    )

    assert compact["pre_surge"]["is_candidate"] is True
    assert compact["pre_surge"]["lane"] == "pre_surge"
    assert compact["pre_surge"]["entry_bias"] == "scout_or_waiting_block"
    assert compact["pre_surge"]["preferred_horizon"] == "mid"


def test_daily_discovery_handles_empty_symbol_directory(tmp_path: Path) -> None:
    analyzer = _SymbolAnalysis()
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=str(tmp_path / "discovery.db")),
        directory_source=_DirectorySource(rows_per_market=0),
        symbol_analysis=analyzer,
    )

    result = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=True))

    assert result["status"] == "empty"
    assert result["selected_count"] == 0
    assert analyzer.calls == []


def test_daily_discovery_persists_partial_analysis_errors(tmp_path: Path) -> None:
    db_path = str(tmp_path / "discovery.db")
    directory = _DirectorySource()
    probe = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=db_path),
        directory_source=directory,
        symbol_analysis=None,
    )
    fail_symbol = probe.select_symbols(trading_day=date(2026, 5, 20))[0]["symbol"]
    analyzer = _SymbolAnalysis(fail_symbol=fail_symbol)
    service = DailyDiscoveryService(
        config=DailyDiscoveryConfig(db_path=db_path),
        directory_source=directory,
        symbol_analysis=analyzer,
    )

    result = asyncio.run(service.run_once(trading_day=date(2026, 5, 20), force=True))
    context = service.latest_context(limit=10)

    assert result["status"] == "partial"
    assert result["summary"]["error_count"] == 1
    assert result["analyzed_count"] == 9
    assert len(analyzer.calls) == 10
    assert any(item["status"] == "error" for item in context["items"])
