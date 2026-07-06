from __future__ import annotations

import re
from typing import Any

from tradecraft.services.jue_research_spine import select_balanced_research_symbols


def is_krx_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def symbols_for_quotes(
    *,
    blocks: list[dict[str, Any]],
    account: dict[str, Any],
    limit: int,
) -> list[str]:
    symbols: list[str] = []

    def append(symbol: Any) -> None:
        code = str(symbol or "").strip()
        if is_krx_symbol(code) and code not in symbols:
            symbols.append(code)

    for row in list(account.get("positions") or []):
        if isinstance(row, dict):
            append(row.get("symbol"))
    for row in blocks:
        if isinstance(row, dict):
            append(row.get("symbol"))
    return symbols[: max(int(limit), 1)]


def manager_symbols(
    *,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    strategy_payload: dict[str, Any],
    limit: int,
) -> list[str]:
    max_items = max(int(limit), 1)
    existing_symbols = symbols_for_quotes(
        blocks=blocks,
        account=account,
        limit=max_items,
    )
    return select_balanced_research_symbols(
        strategy_payload if isinstance(strategy_payload, dict) else {},
        existing_symbols=existing_symbols,
        limit=max_items,
    )
