from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimBroker:
    fee_rate: float = 0.0005
    slippage_bps: float = 1.0

    def execute(self, action: str, symbol: str, quantity: float, mark_price: float) -> dict[str, float | str | bool]:
        side = action.upper()
        qty = max(float(quantity), 0.0)
        mark = max(float(mark_price), 0.0)
        if side not in {"BUY", "SELL"}:
            return {
                "accepted": False,
                "reason": f"unsupported action: {side}",
                "symbol": symbol,
                "quantity": qty,
                "mark_price": mark,
                "fill_price": mark,
                "fee": 0.0,
                "notional": 0.0,
            }

        slip = self.slippage_bps / 10_000.0
        fill_price = mark * (1.0 + slip if side == "BUY" else 1.0 - slip)
        notional = fill_price * qty
        fee = abs(notional) * self.fee_rate
        return {
            "accepted": True,
            "reason": "simulated fill",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "mark_price": mark,
            "fill_price": fill_price,
            "fee": fee,
            "notional": notional,
        }
