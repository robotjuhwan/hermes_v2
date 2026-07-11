from __future__ import annotations

import re
from typing import Any


DEFAULT_CRYPTO_KLINE_INTERVALS: dict[str, int] = {
    "1m": 120,
    "5m": 96,
    "15m": 96,
    "1h": 168,
    "4h": 180,
}


def crypto_research_symbols(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        values = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, list):
        values = [str(item) for item in raw]
    else:
        values = [str(raw)]
    return [
        symbol
        for symbol in dict.fromkeys(item.strip().upper() for item in values)
        if symbol and re.fullmatch(r"[A-Z0-9:_-]{2,30}", symbol)
    ]


def default_crypto_research_symbols(universe: Any) -> list[str]:
    return crypto_research_symbols(universe)


def parse_crypto_kline_intervals(value: Any) -> dict[str, int]:
    intervals: dict[str, int] = {}
    for part in re.split(r"[,;]+", str(value or "")):
        if ":" not in part:
            continue
        key, raw_limit = part.split(":", 1)
        interval = key.strip()
        try:
            limit = int(str(raw_limit).strip())
        except ValueError:
            continue
        if interval and limit > 0:
            intervals[interval] = limit
    return intervals or dict(DEFAULT_CRYPTO_KLINE_INTERVALS)
