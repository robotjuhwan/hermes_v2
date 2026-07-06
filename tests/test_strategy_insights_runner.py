from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tradecraft.runtime.strategy_insights_runner import (
    _strategy_insight_sleep_seconds,
    run_strategy_insight_loop,
)


def test_strategy_insights_runner_writes_state_once(tmp_path: Path) -> None:
    state_path = tmp_path / "strategy_insights.json"

    class _Settings:
        strategy_insight_collect_interval_sec = 30
        strategy_insight_once = True
        strategy_insight_state_path = str(state_path)
        strategy_insight_source_list = [{"source_id": "whale_insight"}]

    class _Collector:
        async def collect_once(self) -> dict:
            return {"status": "ok", "inserted": 1, "sources": []}

    asyncio.run(
        run_strategy_insight_loop(
            settings=_Settings(),
            collector=_Collector(),
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["service"] == "tradecraft-strategy-insights"
    assert payload["status"] == "ok"
    assert payload["result"]["inserted"] == 1


def test_strategy_insights_runner_prunes_collector_repository(tmp_path: Path) -> None:
    state_path = tmp_path / "strategy_insights.json"

    class _Settings:
        strategy_insight_collect_interval_sec = 30
        strategy_insight_error_backoff_sec = 3600
        strategy_insight_once = True
        strategy_insight_state_path = str(state_path)
        strategy_insight_source_list = [{"source_id": "after_close_330"}]
        strategy_insight_retention_days = 45
        strategy_insight_signal_row_cap_per_symbol = 96
        strategy_insight_sidecar_max_lines = 500

    class _Repository:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def prune_history(
            self,
            *,
            retention_days: int,
            signal_row_cap_per_symbol: int,
        ) -> dict:
            self.calls.append(
                {
                    "retention_days": retention_days,
                    "signal_row_cap_per_symbol": signal_row_cap_per_symbol,
                }
            )
            return {
                "status": "ok",
                "retention_days": retention_days,
                "signal_row_cap_per_symbol": signal_row_cap_per_symbol,
                "deleted": {"strategy_signals": 3, "strategy_signals_capped": 2},
            }

    class _Engine:
        def __init__(self) -> None:
            self.insight_repository = _Repository()
            self.compaction_calls: list[dict] = []

        def compact_legacy_jsonl_sidecars(
            self,
            *,
            max_lines_per_source: int,
        ) -> dict:
            self.compaction_calls.append(
                {"max_lines_per_source": max_lines_per_source}
            )
            return {
                "status": "ok",
                "max_lines_per_source": max_lines_per_source,
                "sources": [{"source_id": "after_close_330", "rows": 12}],
            }

    class _Collector:
        def __init__(self) -> None:
            self.engine = _Engine()

        async def collect_once(self) -> dict:
            return {"status": "ok", "inserted": 1, "sources": []}

    collector = _Collector()

    asyncio.run(
        run_strategy_insight_loop(
            settings=_Settings(),
            collector=collector,
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert collector.engine.insight_repository.calls == [
        {"retention_days": 45, "signal_row_cap_per_symbol": 96}
    ]
    assert collector.engine.compaction_calls == [{"max_lines_per_source": 500}]
    assert payload["result"]["retention"]["deleted"]["strategy_signals"] == 3
    assert payload["result"]["retention"]["deleted"]["strategy_signals_capped"] == 2
    assert payload["result"]["sidecar_compaction"]["sources"][0]["rows"] == 12


def test_strategy_insights_runner_backs_off_on_sesiban_404() -> None:
    result = {
        "status": "error",
        "errors": [
            {
                "source_id": "after_close_330",
                "detail": "Client error '404 Not Found' for url",
            }
        ],
    }

    sleep_sec = _strategy_insight_sleep_seconds(
        result,
        interval=900,
        error_backoff_sec=3600,
    )

    assert sleep_sec == 3600
