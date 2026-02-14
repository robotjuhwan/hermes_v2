from __future__ import annotations

from typing import Any

from tradecraft.runtime.contracts import RiskDecision, SessionSignal


class NoopRiskManager:
    name = "noop_risk"

    def assess(self, session_state: dict[str, Any], signal: SessionSignal) -> RiskDecision:
        status = str(session_state.get("status") or "RUNNING").upper()
        if status != "RUNNING":
            return RiskDecision(allow=False, reason="session not running")
        return RiskDecision(
            allow=True,
            reason="skeleton mode: pass-through",
            metadata={"action": signal.action, "symbol": signal.symbol},
        )
