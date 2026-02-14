from __future__ import annotations

import random


class SyntheticReplay:
    def __init__(
        self,
        base_price: float = 100_000_000.0,
        volatility_bps: float = 18.0,
        drift_bps: float = 0.2,
        seed: int = 7,
    ) -> None:
        self.base_price = max(float(base_price), 1.0)
        self.volatility_bps = max(float(volatility_bps), 0.0)
        self.drift_bps = float(drift_bps)
        self._rng = random.Random(seed)
        self._prices: dict[str, float] = {}

    def _symbol_base(self, symbol: str) -> float:
        # Keep symbols separated but deterministic without external market feed.
        shift = (sum(ord(ch) for ch in symbol) % 23) / 100.0
        return self.base_price * (1.0 + shift)

    def _ensure_symbol(self, symbol: str) -> None:
        if symbol not in self._prices:
            self._prices[symbol] = self._symbol_base(symbol)

    def advance(self, symbols: list[str]) -> None:
        for symbol in sorted({s for s in symbols if s}):
            self._ensure_symbol(symbol)
            price = self._prices[symbol]
            shock = self._rng.uniform(-self.volatility_bps, self.volatility_bps) / 10_000.0
            drift = self.drift_bps / 10_000.0
            nxt = price * (1.0 + drift + shock)
            self._prices[symbol] = max(nxt, 1.0)

    def price(self, symbol: str) -> float:
        self._ensure_symbol(symbol)
        return self._prices[symbol]
