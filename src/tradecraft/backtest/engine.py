from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any, Callable

from tradecraft.backtest.clock import VirtualClock
from tradecraft.backtest.replay import SyntheticReplay
from tradecraft.backtest.sim_broker import SimBroker
from tradecraft.runtime.contracts import SessionSignal, Strategy
from tradecraft.runtime.state_store import utc_now_iso
from tradecraft.runtime.strategies import create_strategy


@dataclass
class BacktestConfig:
    cycles: int = 720
    step_sec: int = 60
    speed: float = 120.0
    initial_price: float = 100_000_000.0
    volatility_bps: float = 18.0
    drift_bps: float = 0.2
    fee_rate: float = 0.0005
    slippage_bps: float = 1.0
    seed: int = 7


@dataclass
class BacktestSession:
    state: dict[str, Any]
    strategy: Strategy
    broker: SimBroker
    symbol: str
    signal_count: int = 0
    fill_count: int = 0
    trade_count: int = 0
    tick_count: int = 0
    realized_pnl_krw: float = 0.0
    fees_krw: float = 0.0
    position_qty: float = 0.0
    avg_entry_price: float = 0.0
    last_mark_price: float = 0.0
    last_signal: SessionSignal | None = None

    def _default_qty(self, mark_price: float) -> float:
        max_notional = float(self.state.get("max_notional_krw") or 1_000_000.0)
        if mark_price <= 0:
            return 0.0
        qty = max_notional / mark_price
        return max(qty, 0.0)

    def _apply_buy(self, qty: float, fill_price: float, fee: float) -> None:
        new_qty = self.position_qty + qty
        if new_qty > 0:
            self.avg_entry_price = (
                (self.avg_entry_price * self.position_qty) + (fill_price * qty)
            ) / new_qty
        self.position_qty = new_qty
        self.fees_krw += fee
        self.trade_count += 1

    def _apply_sell(self, qty: float, fill_price: float, fee: float) -> None:
        closable = min(qty, self.position_qty)
        if closable <= 0:
            return
        gross = (fill_price - self.avg_entry_price) * closable
        self.realized_pnl_krw += gross
        self.position_qty -= closable
        if self.position_qty <= 0:
            self.position_qty = 0.0
            self.avg_entry_price = 0.0
        self.fees_krw += fee
        self.trade_count += 1

    def tick(self, cycle: int, event_time: datetime, mark_price: float) -> None:
        self.tick_count += 1
        self.last_mark_price = mark_price
        self.state["last_backtest_time"] = event_time.isoformat()
        self.state["mark_price"] = mark_price
        self.state["strategy_name"] = self.strategy.name

        signal = self.strategy.evaluate(self.state, cycle)
        if signal is None:
            self.state["last_decision"] = {"type": "no_signal", "at": event_time.isoformat()}
            return

        self.signal_count += 1
        self.last_signal = signal
        self.state["last_signal"] = {
            "action": signal.action,
            "symbol": signal.symbol,
            "reason": signal.reason,
            "quantity": signal.quantity,
            "metadata": signal.metadata,
        }

        action = str(signal.action or "").upper()
        if action not in {"BUY", "SELL"}:
            self.state["last_decision"] = {
                "type": "signal_only",
                "reason": f"action={action}",
                "at": event_time.isoformat(),
            }
            return

        quantity = float(signal.quantity) if signal.quantity is not None else self._default_qty(mark_price)
        if quantity <= 0:
            self.state["last_decision"] = {
                "type": "rejected",
                "reason": "quantity=0",
                "at": event_time.isoformat(),
            }
            return

        if action == "SELL" and self.position_qty <= 0:
            self.state["last_decision"] = {
                "type": "rejected",
                "reason": "no_position",
                "at": event_time.isoformat(),
            }
            return

        execution = self.broker.execute(
            action=action,
            symbol=self.symbol,
            quantity=quantity,
            mark_price=mark_price,
        )
        self.state["last_execution"] = execution
        if not bool(execution.get("accepted")):
            self.state["last_decision"] = {
                "type": "rejected",
                "reason": str(execution.get("reason") or "-"),
                "at": event_time.isoformat(),
            }
            return

        fill_price = float(execution.get("fill_price") or mark_price)
        fee = float(execution.get("fee") or 0.0)
        qty = float(execution.get("quantity") or quantity)

        if action == "BUY":
            self._apply_buy(qty, fill_price, fee)
        else:
            self._apply_sell(qty, fill_price, fee)

        self.fill_count += 1
        self.state["last_decision"] = {
            "type": "filled",
            "side": action,
            "qty": qty,
            "at": event_time.isoformat(),
        }
        self.state["trade_count_today"] = self.trade_count

    def summary(self) -> dict[str, Any]:
        unrealized = (self.last_mark_price - self.avg_entry_price) * self.position_qty
        net = self.realized_pnl_krw + unrealized - self.fees_krw
        return {
            "session_id": str(self.state.get("session_id") or "-"),
            "venue_id": str(self.state.get("venue_id") or "-"),
            "mode": str(self.state.get("mode") or "-"),
            "strategy_id": str(self.state.get("strategy_id") or "-"),
            "strategy_name": self.strategy.name,
            "symbol": self.symbol,
            "signals": self.signal_count,
            "fills": self.fill_count,
            "trades": self.trade_count,
            "ticks": self.tick_count,
            "fees_krw": self.fees_krw,
            "realized_pnl_krw": self.realized_pnl_krw,
            "unrealized_pnl_krw": unrealized,
            "net_pnl_krw": net,
            "ending_position_qty": self.position_qty,
            "ending_avg_entry_price": self.avg_entry_price,
            "ending_mark_price": self.last_mark_price,
            "last_decision": self.state.get("last_decision"),
        }


def _resolve_symbol(state: dict[str, Any]) -> str:
    symbol = str(state.get("trade_symbol") or "").strip()
    if symbol:
        return symbol

    targets = list(state.get("targets") or [])
    if targets and isinstance(targets[0], dict):
        t_symbol = str(targets[0].get("symbol") or "").strip()
        if t_symbol:
            return t_symbol

    venue = str(state.get("venue_id") or "portfolio").strip() or "portfolio"
    return venue


class BacktestEngine:
    def __init__(
        self,
        sessions: list[BacktestSession],
        config: BacktestConfig,
        started_at: datetime | None = None,
    ) -> None:
        self.sessions = sessions
        self.config = config
        self.started_at = started_at or datetime.now(timezone.utc)
        self.completed_cycles = 0
        self.clock = VirtualClock(
            step_sec=self.config.step_sec,
            speed=self.config.speed,
            start_at=self.started_at,
        )
        self.replay = SyntheticReplay(
            base_price=self.config.initial_price,
            volatility_bps=self.config.volatility_bps,
            drift_bps=self.config.drift_bps,
            seed=self.config.seed,
        )

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @classmethod
    def from_session_rows(cls, rows: list[dict[str, Any]], config: BacktestConfig) -> "BacktestEngine":
        sessions: list[BacktestSession] = []
        broker = SimBroker(fee_rate=config.fee_rate, slippage_bps=config.slippage_bps)
        for row in rows:
            state = dict(row)
            strategy = create_strategy(state)
            symbol = _resolve_symbol(state)
            sessions.append(
                BacktestSession(
                    state=state,
                    strategy=strategy,
                    broker=broker,
                    symbol=symbol,
                )
            )
        return cls(sessions=sessions, config=config)

    def _aggregate(self) -> dict[str, float]:
        realized = 0.0
        unrealized = 0.0
        fees = 0.0
        for session in self.sessions:
            realized += session.realized_pnl_krw
            unrealized += (session.last_mark_price - session.avg_entry_price) * session.position_qty
            fees += session.fees_krw
        return {
            "realized_pnl_krw": realized,
            "unrealized_pnl_krw": unrealized,
            "fees_krw": fees,
            "net_pnl_krw": realized + unrealized - fees,
        }

    def _progress_snapshot(self, cycle: int, total_cycles: int, event_time: datetime) -> dict[str, Any]:
        return {
            "cycle": cycle,
            "total_cycles": total_cycles,
            "progress_pct": round((cycle / total_cycles) * 100.0, 2),
            "time": event_time.isoformat(),
            "aggregate": self._aggregate(),
            "sessions": [
                {
                    "session_id": str(session.state.get("session_id") or "-"),
                    "symbol": session.symbol,
                    "signals": session.signal_count,
                    "fills": session.fill_count,
                    "trades": session.trade_count,
                    "net_pnl_krw": session.summary().get("net_pnl_krw"),
                }
                for session in self.sessions
            ],
        }

    def run(
        self,
        on_cycle: Callable[[dict[str, Any]], None] | None = None,
        emit_interval: int = 1,
        stop_event: Event | None = None,
    ) -> dict[str, Any]:
        cycles = max(int(self.config.cycles), 1)
        interval = max(int(emit_interval), 1)
        self.completed_cycles = 0
        for cycle in range(1, cycles + 1):
            if stop_event is not None and stop_event.is_set():
                break
            event_time = self.clock.tick()
            symbols = [session.symbol for session in self.sessions]
            self.replay.advance(symbols)
            for session in self.sessions:
                mark_price = self.replay.price(session.symbol)
                session.tick(cycle=cycle, event_time=event_time, mark_price=mark_price)
            self.completed_cycles = cycle
            if on_cycle is not None and (cycle % interval == 0 or cycle == cycles):
                on_cycle(self._progress_snapshot(cycle=cycle, total_cycles=cycles, event_time=event_time))

        return self.result()

    def result(self) -> dict[str, Any]:
        session_rows = [session.summary() for session in self.sessions]
        total_signals = sum(int(row.get("signals") or 0) for row in session_rows)
        total_fills = sum(int(row.get("fills") or 0) for row in session_rows)
        total_cycles = max(int(self.config.cycles), 1)
        status = "completed" if self.completed_cycles >= total_cycles else "stopped"
        return {
            "updated_at": utc_now_iso(),
            "backtest": {
                "service": "tradecraft-backtest",
                "status": status,
                "cycles": total_cycles,
                "completed_cycles": self.completed_cycles,
                "step_sec": max(int(self.config.step_sec), 1),
                "speed": max(float(self.config.speed), 0.01),
                "simulated_seconds": self.clock.simulated_seconds,
                "wall_seconds_equivalent": self.clock.wall_seconds_equivalent,
                "seed": int(self.config.seed),
                "session_count": self.session_count,
                "total_signals": total_signals,
                "total_fills": total_fills,
            },
            "sessions": session_rows,
        }
