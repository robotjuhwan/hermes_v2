from __future__ import annotations

from tradecraft.runtime.engine import RuntimeEngine


def test_runtime_engine_builds_snapshot_with_runtime_metadata() -> None:
    rows = [
        {
            "session_id": "s_short",
            "venue_id": "upbit",
            "mode": "short_term",
            "cycle_sec": 5,
            "trade_symbol": "BTC/KRW",
        },
        {
            "session_id": "s_balance",
            "venue_id": "binance",
            "mode": "mid_long_term",
        },
    ]

    engine = RuntimeEngine.from_session_rows(rows, base_interval_sec=5)
    snapshot = engine.build_snapshot(cycle=15)

    assert snapshot["runtime"]["status"] == "running"
    assert snapshot["runtime"]["engine"] == "session_heartbeat"
    assert snapshot["runtime"]["execution_mode"] == "skeleton_noop"
    assert snapshot["runtime"]["executes_orders"] is False
    assert snapshot["runtime"]["base_interval_sec"] == 5
    assert snapshot["runtime"]["sessions"] == 2
    assert len(snapshot["sessions"]) == 2
    assert snapshot["sessions"][0]["status"] == "RUNNING"
    assert snapshot["sessions"][0]["strategy_name"] == "noop_short_term"
    assert snapshot["sessions"][0]["last_signal"]["symbol"] == "BTC/KRW"


def test_runtime_engine_marks_heartbeat_when_session_cycle_is_not_due() -> None:
    rows = [
        {
            "session_id": "s_short",
            "venue_id": "upbit",
            "mode": "short_term",
            "cycle_sec": 20,
            "trade_symbol": "BTC/KRW",
        }
    ]
    engine = RuntimeEngine.from_session_rows(rows, base_interval_sec=5)

    first = engine.build_snapshot(cycle=1)
    second = engine.build_snapshot(cycle=4)

    assert first["sessions"][0]["last_decision"]["type"] == "heartbeat"
    assert second["sessions"][0]["last_decision"]["type"] == "no_signal"
