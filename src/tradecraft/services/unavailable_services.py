from __future__ import annotations

from typing import Any


class UnavailableCryptoMarketResearchService:
    def __init__(self, *, reason: str, db_path: str) -> None:
        self.reason = reason
        self.db_path = db_path

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "db_path": self.db_path,
            "snapshot_count": 0,
            "candidate_count": 0,
            "reason": self.reason,
        }

    def latest_context(
        self,
        symbols: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "symbols": symbols or [],
            "limit": limit,
            "items": [],
            "market_regime": {"status": "missing", "regime": "unknown"},
            "observed_symbol_count": len(symbols or []),
            "focus_symbol_count": 0,
            "candidates": [],
            "symbol_notes": {},
            "features": {},
            "reason": self.reason,
        }

    async def collect_market_structure(self, symbols: list[str]) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "symbols": symbols,
            "reason": self.reason,
        }

    async def run_research_once(
        self,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "symbols": symbols or [],
            "reason": self.reason,
        }


class UnavailableCryptoAlphaService:
    def __init__(self, *, reason: str, db_path: str) -> None:
        self.reason = reason
        self.db_path = db_path

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "db_path": self.db_path,
            "sources": 0,
            "snapshots": 0,
            "events": 0,
            "outcomes": 0,
            "hypotheses": 0,
            "reason": self.reason,
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "available": False,
            "scope": "binance_crypto_alpha",
            "symbols": symbols or [],
            "limit": limit,
            "events": [],
            "similar_outcomes": [],
            "scorecards": [],
            "active_lessons": [],
            "contradictions": [],
            "data_gaps": ["crypto_alpha_unavailable"],
            "reason": self.reason,
        }

    async def collect_once(self) -> dict[str, Any]:
        return {"status": "skipped", "available": False, "reason": self.reason}

    async def label_due_outcomes(self) -> dict[str, Any]:
        return {
            "status": "skipped",
            "available": False,
            "reason": self.reason,
            "labeled": 0,
        }
