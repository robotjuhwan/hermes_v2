from __future__ import annotations

from typing import Any

from tradecraft.backtest.engine import BacktestConfig, BacktestEngine
from tradecraft.runtime import register_strategy
from tradecraft.runtime.contracts import SessionSignal


class FlipStrategy:
    name = "flip_strategy"
    mode = "short_term"

    def evaluate(self, session_state: dict[str, Any], cycle: int) -> SessionSignal | None:
        action = "BUY" if cycle % 2 else "SELL"
        return SessionSignal(
            action=action,
            symbol=str(session_state.get("trade_symbol") or "BTC/KRW"),
            reason="test signal",
            quantity=0.01,
        )


def test_backtest_engine_generates_session_summary() -> None:
    register_strategy("test_flip", lambda _state: FlipStrategy())
    rows = [
        {
            "session_id": "bt_upbit_1",
            "venue_id": "upbit",
            "mode": "short_term",
            "strategy_id": "test_flip",
            "trade_symbol": "BTC/KRW",
        }
    ]
    config = BacktestConfig(
        cycles=8,
        step_sec=60,
        speed=120.0,
        initial_price=100_000_000.0,
        seed=13,
    )
    engine = BacktestEngine.from_session_rows(rows=rows, config=config)

    result = engine.run()

    assert result["backtest"]["status"] == "completed"
    assert result["backtest"]["session_count"] == 1
    assert result["backtest"]["cycles"] == 8
    assert result["backtest"]["total_signals"] == 8
    assert result["backtest"]["total_fills"] == 8
    session = result["sessions"][0]
    assert session["session_id"] == "bt_upbit_1"
    assert session["signals"] == 8
    assert session["fills"] == 8
    assert session["trades"] == 8
    assert session["ticks"] == 8
    assert session["fees_krw"] > 0
    assert isinstance(session["net_pnl_krw"], float)
