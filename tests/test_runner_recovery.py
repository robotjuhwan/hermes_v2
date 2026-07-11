from __future__ import annotations

from tradecraft.runtime.runner_recovery import recover_runners_rolling


def _row(*, pid: int, started: float, stale: bool) -> dict[str, object]:
    return {
        "alive": True,
        "pid": pid,
        "started_at_epoch": started,
        "pid_file_status": "ok",
        "stale_process": stale,
        "stale_runtime_state": False,
    }


def test_rolling_recovery_verifies_each_runner_before_next() -> None:
    events: list[str] = []
    rows = {
        "jue_wiki": iter(
            [
                _row(pid=10, started=1, stale=True),
                _row(pid=11, started=2, stale=False),
            ]
        ),
        "control": iter(
            [
                _row(pid=20, started=1, stale=True),
                _row(pid=21, started=2, stale=False),
            ]
        ),
    }

    result = recover_runners_rolling(
        ["control", "jue_wiki"],
        restart_one=lambda key: events.append(f"restart:{key}"),
        status_provider=lambda key: next(rows[key]),
        sleep=lambda _: None,
    )

    assert events == ["restart:jue_wiki", "restart:control"]
    assert result["verified_keys"] == ["jue_wiki", "control"]
    assert result["status"] == "ok"


def test_rolling_recovery_stops_after_failed_verification() -> None:
    events: list[str] = []
    result = recover_runners_rolling(
        ["kis_block_trader", "control"],
        restart_one=lambda key: events.append(f"restart:{key}"),
        status_provider=lambda key: {"alive": False, "status": "stopped"},
        verify_timeout_sec=0,
        sleep=lambda _: None,
    )

    assert result["status"] == "verification_failed"
    assert events == ["restart:kis_block_trader"]
    assert result["failed_key"] == "kis_block_trader"
