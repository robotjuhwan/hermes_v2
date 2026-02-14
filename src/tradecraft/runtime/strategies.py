from __future__ import annotations

from typing import Any, Callable

from tradecraft.runtime.contracts import SessionSignal, Strategy


class NoopShortTermStrategy:
    name = "noop_short_term"
    mode = "short_term"

    def evaluate(self, session_state: dict[str, Any], cycle: int) -> SessionSignal | None:
        symbol = str(session_state.get("trade_symbol") or "").strip()
        if not symbol:
            return None
        if cycle % 15 != 0:
            return None
        return SessionSignal(
            action="HOLD",
            symbol=symbol,
            reason="skeleton heartbeat",
            metadata={"cycle": cycle},
        )


class NoopBalanceStrategy:
    name = "noop_balance"
    mode = "mid_long_term"

    def evaluate(self, session_state: dict[str, Any], cycle: int) -> SessionSignal | None:
        if cycle % 60 != 0:
            return None
        return SessionSignal(
            action="HOLD",
            symbol=str(session_state.get("venue_id") or "portfolio"),
            reason="skeleton rebalance check",
            metadata={"cycle": cycle},
        )


StrategyFactory = Callable[[dict[str, Any]], Strategy]

_REGISTRY: dict[str, StrategyFactory] = {
    "short_term": lambda _session: NoopShortTermStrategy(),
    "mid_long_term": lambda _session: NoopBalanceStrategy(),
    "noop_short_term": lambda _session: NoopShortTermStrategy(),
    "noop_balance": lambda _session: NoopBalanceStrategy(),
}


def register_strategy(name: str, factory: StrategyFactory) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("strategy name is required")
    _REGISTRY[key] = factory


def create_strategy(session_state: dict[str, Any]) -> Strategy:
    strategy_key = str(session_state.get("strategy_id") or "").strip().lower()
    mode_key = str(session_state.get("mode") or "").strip().lower()

    if strategy_key and strategy_key in _REGISTRY:
        return _REGISTRY[strategy_key](session_state)

    if mode_key in _REGISTRY:
        return _REGISTRY[mode_key](session_state)

    return NoopBalanceStrategy()
