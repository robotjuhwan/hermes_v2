from __future__ import annotations

import time
from typing import Any

from tradecraft.backtest.engine import BacktestConfig
from tradecraft.backtest.live_manager import BacktestLiveManager
from tradecraft.runtime import register_strategy
from tradecraft.runtime.contracts import SessionSignal


class _FlipLiveStrategy:
    name = "flip_live"
    mode = "short_term"

    def evaluate(self, session_state: dict[str, Any], cycle: int) -> SessionSignal | None:
        action = "BUY" if cycle % 2 else "SELL"
        return SessionSignal(
            action=action,
            symbol=str(session_state.get("trade_symbol") or "BTC/KRW"),
            reason="live manager test",
            quantity=0.01,
        )


def test_backtest_live_manager_runs_until_completed(tmp_path) -> None:
    register_strategy("test_flip_live", lambda _state: _FlipLiveStrategy())

    manager = BacktestLiveManager(
        state_path=str(tmp_path / "backtest_live.json"),
        result_path=str(tmp_path / "backtest_result.json"),
    )
    rows = [
        {
            "session_id": "live_s1",
            "venue_id": "upbit",
            "mode": "short_term",
            "strategy_id": "test_flip_live",
            "trade_symbol": "BTC/KRW",
        }
    ]
    config = BacktestConfig(cycles=6, step_sec=30, speed=120.0)

    started = manager.start(
        session_rows=rows,
        config=config,
        scenario="baseline",
        session_source="test",
        emit_interval=1,
    )
    assert started["status"] == "running"

    deadline = time.time() + 3.0
    while time.time() < deadline:
        status = manager.status()
        if status.get("job", {}).get("status") != "running":
            break
        time.sleep(0.05)

    final_state = manager.status()
    assert final_state["job"]["status"] == "completed"
    assert final_state["progress"]["cycle"] == 6
    assert final_state["result"]["backtest"]["total_fills"] >= 1
