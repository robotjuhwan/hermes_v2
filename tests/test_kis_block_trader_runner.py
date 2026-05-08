from __future__ import annotations

import asyncio
from pathlib import Path

from tradecraft.runtime.kis_block_trader_runner import run_kis_block_trader_loop


class _Settings:
    def __init__(self, state_path: Path) -> None:
        self.kis_block_trader_state_path = str(state_path)
        self.kis_block_trader_rule_interval_sec = 1
        self.kis_block_trader_manager_interval_sec = 60
        self.kis_block_trader_once = True


class _Trader:
    def clock(self) -> dict:
        return {"session": "closed"}

    async def executor_tick(self) -> dict:
        return {"status": "skipped", "actions": []}


def test_kis_block_trader_runner_writes_state_once(tmp_path: Path) -> None:
    state_path = tmp_path / "kis_block_trader.json"

    async def no_sleep(seconds: float) -> None:
        _ = seconds

    asyncio.run(
        run_kis_block_trader_loop(
            settings=_Settings(state_path),  # type: ignore[arg-type]
            trader=_Trader(),  # type: ignore[arg-type]
            sleep=no_sleep,
        )
    )

    assert state_path.exists()
    assert "tradecraft-kis-block-trader" in state_path.read_text(encoding="utf-8")
