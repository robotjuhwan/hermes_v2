from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tradecraft.runtime.strategy_insights_runner import run_strategy_insight_loop


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
