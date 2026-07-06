from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.runtime.crypto_pattern_lab_runner import (
    build_crypto_pattern_lab_service,
    run_crypto_pattern_lab_loop,
    select_pattern_lab_symbols,
)
from tradecraft.services.crypto_market_research import CryptoMarketResearchRepository


def test_crypto_pattern_lab_runner_writes_state_once(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "pattern_lab.json"
    calls: list[dict[str, Any]] = []

    class FakeService:
        def run_once(self, *, symbols: list[str]) -> dict[str, Any]:
            calls.append({"symbols": symbols})
            return {"status": "ok", "imported_source_count": 1, "backtest_count": 2}

        def status(self) -> dict[str, Any]:
            return {"status": "ok", "pattern_count": 2, "backtest_count": 2}

    class Settings:
        crypto_pattern_lab_enabled = True
        crypto_pattern_lab_once = True
        crypto_pattern_lab_state_path = str(state_path)
        crypto_market_research_db_path = str(tmp_path / "crypto_research.db")
        crypto_pattern_lab_interval_sec = 1
        crypto_market_research_universe = "BTCUSDT,ETHUSDT"
        crypto_pattern_lab_max_symbols = 1

    asyncio.run(
        run_crypto_pattern_lab_loop(
            settings=Settings(),
            service=FakeService(),
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    assert calls == [{"symbols": ["BTCUSDT"]}]
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["result"]["backtest_count"] == 2
    assert payload["symbol_selection"]["selected_count"] == 1


def test_crypto_pattern_lab_runner_records_cycle_error_without_crashing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "pattern_lab.json"

    class FakeService:
        def run_once(self, *, symbols: list[str]) -> dict[str, Any]:
            raise sqlite3.OperationalError("database is locked")

        def status(self) -> dict[str, Any]:
            return {"status": "degraded", "pattern_count": 0}

    class Settings:
        crypto_pattern_lab_enabled = True
        crypto_pattern_lab_once = True
        crypto_pattern_lab_state_path = str(state_path)
        crypto_market_research_db_path = str(tmp_path / "crypto_research.db")
        crypto_pattern_lab_interval_sec = 1
        crypto_market_research_universe = "BTCUSDT,ETHUSDT"
        crypto_pattern_lab_max_symbols = 1

    asyncio.run(
        run_crypto_pattern_lab_loop(
            settings=Settings(),
            service=FakeService(),
            sleep=lambda _: asyncio.sleep(0),
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["status"] == "error"
    assert payload["result"]["status"] == "error"
    assert payload["result"]["error_type"] == "OperationalError"
    assert "database is locked" in payload["result"]["error_message"]


def test_build_crypto_pattern_lab_service_propagates_retention_settings(
    tmp_path: Path,
) -> None:
    class Settings:
        crypto_pattern_lab_db_path = str(tmp_path / "pattern_lab.db")
        crypto_market_research_db_path = str(tmp_path / "crypto_research.db")
        crypto_pattern_lab_enabled = True
        crypto_pattern_lab_strategy_paths = ""
        crypto_pattern_lab_freqtrade_data_paths = ""
        crypto_pattern_lab_max_symbols = 17
        crypto_pattern_lab_intervals = "5m,1h"
        crypto_pattern_lab_lookback_bars = 321
        crypto_pattern_lab_context_limit = 9
        crypto_pattern_lab_retention_days = 33
        crypto_pattern_lab_backtests_per_tuple_retention = 2
        crypto_pattern_lab_optimizer_runs_per_tuple_retention = 3
        crypto_pattern_lab_optimizer_trials_per_run_retention = 5
        crypto_pattern_lab_max_backtest_rows = 1234
        crypto_pattern_lab_max_optimizer_runs = 234
        crypto_pattern_lab_max_optimizer_trials = 3456
        crypto_pattern_lab_optimizer_enabled = True
        crypto_pattern_lab_optimizer_max_scorecards = 11
        crypto_pattern_lab_optimizer_max_trials_per_scorecard = 7

    service = build_crypto_pattern_lab_service(Settings())

    assert service.config.retention_days == 33
    assert service.config.backtests_per_tuple_retention == 2
    assert service.config.optimizer_runs_per_tuple_retention == 3
    assert service.config.optimizer_trials_per_run_retention == 5
    assert service.config.max_backtest_rows == 1234
    assert service.config.max_optimizer_runs == 234
    assert service.config.max_optimizer_trials == 3456


def test_pattern_lab_symbol_selection_prioritizes_research_candidates(
    tmp_path: Path,
) -> None:
    research_db = tmp_path / "crypto_research.db"
    repository = CryptoMarketResearchRepository(research_db)
    repository.upsert_candidate(
        {
            "symbol": "WIFUSDT",
            "market": "futures",
            "stance": "long",
            "horizon": "futures",
            "score": 91,
            "confidence": 0.8,
            "reason_md": "volatile candidate",
        },
        source_run_id=1,
    )
    repository.upsert_features(
        "PEPEUSDT",
        {
            "price": 0.00001,
            "change_pct_24h": 18.0,
            "quote_volume_usdt": 50_000_000,
            "volume_expansion_ratio": 2.2,
        },
    )
    repository.upsert_candidate(
        {
            "symbol": "币安人生USDT",
            "market": "futures",
            "stance": "long",
            "horizon": "futures",
            "score": 99,
            "confidence": 0.9,
            "reason_md": "invalid symbol text from research",
        },
        source_run_id=1,
    )

    class Settings:
        crypto_market_research_db_path = str(research_db)
        crypto_market_research_universe = "BTCUSDT,ETHUSDT,SOLUSDT"

    symbols, meta = select_pattern_lab_symbols(Settings(), max_symbols=5)

    assert symbols[:3] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert "WIFUSDT" in symbols
    assert "PEPEUSDT" in symbols
    assert "币安人生USDT" not in symbols
    assert "USDT" not in symbols
    assert meta["status"] == "research_db"
    assert meta["dynamic_count"] == 2
