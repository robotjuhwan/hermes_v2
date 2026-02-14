from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class VirtualClock:
    step_sec: int
    speed: float
    start_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current: datetime = field(init=False)
    ticks: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.step_sec = max(int(self.step_sec), 1)
        self.speed = max(float(self.speed), 0.01)
        if self.start_at.tzinfo is None:
            self.start_at = self.start_at.replace(tzinfo=timezone.utc)
        self.current = self.start_at

    def tick(self) -> datetime:
        self.current = self.current + timedelta(seconds=self.step_sec)
        self.ticks += 1
        return self.current

    @property
    def simulated_seconds(self) -> int:
        return self.ticks * self.step_sec

    @property
    def wall_seconds_equivalent(self) -> float:
        return self.simulated_seconds / self.speed
