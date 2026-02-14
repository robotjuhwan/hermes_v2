from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tradecraft.runtime.state_store import utc_now_iso


def _resolve_symbol(row: dict[str, Any]) -> str:
    symbol = str(row.get("trade_symbol") or "").strip()
    if symbol:
        return symbol
    targets = list(row.get("targets") or [])
    if targets and isinstance(targets[0], dict):
        candidate = str(targets[0].get("symbol") or "").strip()
        if candidate:
            return candidate
    return str(row.get("venue_id") or "portfolio").strip() or "portfolio"


class BacktestDataRegistry:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"updated_at": utc_now_iso(), "symbols": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"updated_at": utc_now_iso(), "symbols": {}}
        if not isinstance(payload, dict):
            return {"updated_at": utc_now_iso(), "symbols": {}}
        symbols = payload.get("symbols")
        if not isinstance(symbols, dict):
            payload["symbols"] = {}
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = utc_now_iso()
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def observe_sessions(self, rows: list[dict[str, Any]], source: str) -> dict[str, Any]:
        payload = self._read()
        symbols = payload.setdefault("symbols", {})
        now = utc_now_iso()

        for row in rows:
            symbol = _resolve_symbol(row)
            venue_id = str(row.get("venue_id") or "unknown")
            item = symbols.get(symbol)
            if not isinstance(item, dict):
                item = {
                    "symbol": symbol,
                    "venue_ids": [],
                    "samples": 0,
                    "first_seen": now,
                    "last_seen": now,
                    "last_source": source,
                }
            venues = set(str(v) for v in list(item.get("venue_ids") or []))
            venues.add(venue_id)
            item["venue_ids"] = sorted(venues)
            item["samples"] = int(item.get("samples") or 0) + 1
            item["last_seen"] = now
            item["last_source"] = source
            symbols[symbol] = item

        self._write(payload)
        return self.status()

    def status(self) -> dict[str, Any]:
        payload = self._read()
        symbols = payload.get("symbols") if isinstance(payload.get("symbols"), dict) else {}
        return {
            "updated_at": str(payload.get("updated_at") or utc_now_iso()),
            "symbol_count": len(symbols),
            "symbols": sorted(symbols.keys())[:100],
        }
