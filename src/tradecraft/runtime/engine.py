from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradecraft.runtime.brokers import create_broker
from tradecraft.runtime.contracts import Broker, RiskManager, SessionSignal, Strategy
from tradecraft.runtime.risk import NoopRiskManager
from tradecraft.runtime.state_store import utc_now_iso
from tradecraft.runtime.strategies import create_strategy


@dataclass
class RuntimeSession:
    state: dict[str, Any]
    strategy: Strategy
    broker: Broker
    risk_manager: RiskManager
    base_interval_sec: int
    last_signal: SessionSignal | None = None

    def _cycle_mod(self) -> int:
        cycle_sec = int(self.state.get("cycle_sec") or self.base_interval_sec)
        cycle_sec = max(cycle_sec, 1)
        if cycle_sec <= self.base_interval_sec:
            return 1
        return max((cycle_sec + self.base_interval_sec - 1) // self.base_interval_sec, 1)

    def _should_run(self, cycle: int) -> bool:
        return cycle % self._cycle_mod() == 0

    def _run_signal(self, cycle: int) -> None:
        signal = self.strategy.evaluate(self.state, cycle)
        if signal is None:
            self.state["last_decision"] = {
                "type": "no_signal",
                "at": utc_now_iso(),
                "strategy": self.strategy.name,
            }
            return

        self.last_signal = signal
        self.state["last_signal"] = {
            "action": signal.action,
            "symbol": signal.symbol,
            "reason": signal.reason,
            "quantity": signal.quantity,
            "metadata": signal.metadata,
        }

        risk = self.risk_manager.assess(self.state, signal)
        self.state["last_risk"] = {
            "allow": risk.allow,
            "reason": risk.reason,
            "metadata": risk.metadata,
            "name": self.risk_manager.name,
            "at": utc_now_iso(),
        }
        if not risk.allow:
            self.state["last_decision"] = {
                "type": "blocked",
                "reason": risk.reason,
                "at": utc_now_iso(),
            }
            return

        execution = self.broker.execute(signal)
        self.state["last_execution"] = execution
        self.state["last_decision"] = {
            "type": "executed" if execution.get("accepted") else "rejected",
            "reason": str(execution.get("reason") or "-"),
            "at": utc_now_iso(),
        }

    def tick(self, cycle: int) -> dict[str, Any]:
        now = utc_now_iso()
        self.state["last_heartbeat"] = utc_now_iso()
        self.state.setdefault("status", "RUNNING")
        self.state["loop_cycle"] = cycle
        self.state["strategy_name"] = self.strategy.name

        if not self._should_run(cycle):
            self.state["last_decision"] = {
                "type": "heartbeat",
                "reason": "cycle not due",
                "at": now,
            }
            return dict(self.state)

        self._run_signal(cycle)

        return dict(self.state)


class RuntimeEngine:
    def __init__(
        self,
        sessions: list[RuntimeSession],
        base_interval_sec: int,
        service_name: str = "tradecraft-runtime",
    ) -> None:
        self.sessions = sessions
        self.base_interval_sec = max(int(base_interval_sec), 1)
        self.service_name = service_name

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @classmethod
    def from_session_rows(
        cls,
        rows: list[dict[str, Any]],
        base_interval_sec: int = 5,
        service_name: str = "tradecraft-runtime",
    ) -> "RuntimeEngine":
        interval = max(int(base_interval_sec), 1)
        sessions: list[RuntimeSession] = []
        for row in rows:
            session_state = dict(row)
            strategy: Strategy = create_strategy(session_state)
            broker: Broker = create_broker(session_state)
            risk_manager: RiskManager = NoopRiskManager()
            sessions.append(
                RuntimeSession(
                    state=session_state,
                    strategy=strategy,
                    broker=broker,
                    risk_manager=risk_manager,
                    base_interval_sec=interval,
                )
            )

        return cls(sessions=sessions, base_interval_sec=interval, service_name=service_name)

    def build_snapshot(self, cycle: int) -> dict[str, Any]:
        session_rows = [session.tick(cycle=cycle) for session in self.sessions]
        return {
            "updated_at": utc_now_iso(),
            "runtime": {
                "service": self.service_name,
                "status": "running",
                "cycle": cycle,
                "engine": "session_heartbeat",
                "role": "session_state_monitor",
                "role_label": "세션 상태/heartbeat",
                "description": (
                    "거래소별 전략 세션의 heartbeat, risk, skeleton execution "
                    "상태를 기록한다."
                ),
                "execution_mode": "skeleton_noop",
                "executes_orders": False,
                "base_interval_sec": self.base_interval_sec,
                "sessions": self.session_count,
                "managed_capabilities": [
                    "session_heartbeat",
                    "strategy_skeleton",
                    "risk_state_snapshot",
                ],
                "externalized_capabilities": [
                    "research_and_reports:intelligence",
                    "market_signals:strategy_insights",
                    "kis_trading:kis_trader",
                ],
            },
            "sessions": session_rows,
        }
