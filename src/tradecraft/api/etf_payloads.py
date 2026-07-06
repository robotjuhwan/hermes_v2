from __future__ import annotations

import re
from typing import Any, Callable

from tradecraft.api.etf import etf_universe_item_payload


def build_etf_research_candidates_payload(
    *,
    repository: Any,
    configured: list[Any],
    universe_item_payload: Callable[[Any], dict[str, Any]] = etf_universe_item_payload,
) -> list[dict[str, Any]]:
    rows = repository.list_universe()
    universe_symbols = {str(item.symbol) for item in configured}
    if universe_symbols:
        by_symbol = {str(row["symbol"]): row for row in rows}
        rows = [
            by_symbol.get(str(item.symbol))
            or {
                **universe_item_payload(item),
                "updated_at": "",
            }
            for item in configured
        ]
    return [
        {
            **row,
            "latest_snapshot": repository.latest_snapshot(str(row["symbol"])),
            "latest_score": repository.latest_score(str(row["symbol"])),
        }
        for row in rows
        if not universe_symbols or str(row["symbol"]) in universe_symbols
    ]


def merge_etf_items_payload(
    *groups: list[Any],
    limit: int = 200,
) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            symbol = str(getattr(item, "symbol", "") or "").strip()
            if not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
                continue
            seen.add(symbol)
            out.append(item)
            if len(out) >= max(int(limit), 1):
                return out
    return out
