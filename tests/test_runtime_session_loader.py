from __future__ import annotations

import json

from tradecraft.runtime.session_loader import load_runtime_sessions


def test_load_runtime_sessions_default_when_path_empty() -> None:
    sessions, source = load_runtime_sessions("")

    assert source == "default"
    assert sessions
    assert any(row["mode"] == "short_term" for row in sessions)
    assert any(row["mode"] == "mid_long_term" for row in sessions)


def test_load_runtime_sessions_from_file(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "sessions": [
                    {
                        "session_id": "custom_short",
                        "venue_id": "upbit",
                        "mode": "short_term",
                        "trade_symbol": "ETH/KRW",
                        "cycle_sec": 10,
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sessions, source = load_runtime_sessions(str(path))

    assert source.startswith("file:")
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "custom_short"
    assert sessions[0]["status"] == "RUNNING"
    assert sessions[0]["strategy_id"] == "noop_short_term"
    assert sessions[0]["trade_symbol"] == "ETH/KRW"


def test_load_runtime_sessions_fallback_on_invalid_file(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("{invalid", encoding="utf-8")

    sessions, source = load_runtime_sessions(str(path))

    assert "invalid file" in source
    assert sessions
