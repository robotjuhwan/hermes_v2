from __future__ import annotations

from typing import Any, Callable

from tradecraft.runtime.contracts import Broker, SessionSignal


class NoopBroker:
    def __init__(self, venue_id: str) -> None:
        self.venue_id = venue_id

    def execute(self, signal: SessionSignal) -> dict[str, object]:
        return {
            "accepted": True,
            "executed": False,
            "venue_id": self.venue_id,
            "action": signal.action,
            "symbol": signal.symbol,
            "reason": "skeleton mode: execution disabled",
        }


BrokerFactory = Callable[[dict[str, Any]], Broker]

_REGISTRY: dict[str, BrokerFactory] = {}


def register_broker(venue_id: str, factory: BrokerFactory) -> None:
    key = venue_id.strip().lower()
    if not key:
        raise ValueError("venue_id is required")
    _REGISTRY[key] = factory


def create_broker(session_state: dict[str, Any]) -> Broker:
    venue_id = str(session_state.get("venue_id") or "unknown").strip().lower()
    if venue_id in _REGISTRY:
        return _REGISTRY[venue_id](session_state)
    return NoopBroker(venue_id=venue_id or "unknown")
