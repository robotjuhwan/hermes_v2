from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SessionSignal:
    action: str
    symbol: str
    reason: str
    quantity: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskDecision:
    allow: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(Protocol):
    name: str
    mode: str

    def evaluate(self, session_state: dict[str, Any], cycle: int) -> SessionSignal | None:
        ...


class RiskManager(Protocol):
    name: str

    def assess(self, session_state: dict[str, Any], signal: SessionSignal) -> RiskDecision:
        ...


class Broker(Protocol):
    venue_id: str

    def execute(self, signal: SessionSignal) -> dict[str, Any]:
        ...
