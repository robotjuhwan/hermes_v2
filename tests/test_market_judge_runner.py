from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tradecraft.runtime.market_judge_runner import run_market_judge_loop


def test_market_judge_runner_writes_state_once(tmp_path: Path) -> None:
    state_path = tmp_path / "market_judge.json"

    class _Settings:
        market_judge_state_path = str(state_path)
        market_quote_interval_sec = 15
        market_judge_interval_sec = 60
        market_judge_once = True

    class _Engine:
        def clock(self) -> dict:
            return {"session": "regular"}

        async def run_once(self, *, use_llm: bool = True) -> dict:
            return {
                "status": "ok",
                "llm_used": use_llm,
                "judgments": [{"symbol": "005930"}],
            }

    asyncio.run(
        run_market_judge_loop(
            settings=_Settings(),  # type: ignore[arg-type]
            engine=_Engine(),  # type: ignore[arg-type]
        )
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["service"] == "tradecraft-market-judge"
    assert payload["status"] == "ok"
    assert payload["result"]["llm_used"] is True
