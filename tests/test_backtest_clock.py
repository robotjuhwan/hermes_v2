from __future__ import annotations

from tradecraft.backtest.clock import VirtualClock


def test_virtual_clock_applies_acceleration() -> None:
    clock = VirtualClock(step_sec=60, speed=120.0)
    clock.tick()
    clock.tick()
    clock.tick()

    assert clock.ticks == 3
    assert clock.simulated_seconds == 180
    assert clock.wall_seconds_equivalent == 1.5
