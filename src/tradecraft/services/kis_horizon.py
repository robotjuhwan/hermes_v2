from __future__ import annotations

import re
from typing import Any

BLOCK_HORIZONS = {"short", "mid", "long", "core_etf"}
ACTIVE_BLOCK_STATUSES = {"entry_pending", "open", "exit_pending"}
HORIZON_COLORS = {
    "short": "short",
    "mid": "mid",
    "long": "long",
    "core_etf": "etf",
    "cash": "cash",
}
HORIZON_ALIASES = {
    "short": "short",
    "shortterm": "short",
    "short_term": "short",
    "short-term": "short",
    "intraday": "short",
    "swing": "short",
    "day": "short",
    "단기": "short",
    "mid": "mid",
    "medium": "mid",
    "mediumterm": "mid",
    "medium_term": "mid",
    "medium-term": "mid",
    "midterm": "mid",
    "mid_term": "mid",
    "mid-term": "mid",
    "중기": "mid",
    "long": "long",
    "longterm": "long",
    "long_term": "long",
    "long-term": "long",
    "장기": "long",
    "core": "core_etf",
    "coreetf": "core_etf",
    "core_etf": "core_etf",
    "core-etf": "core_etf",
    "etf": "core_etf",
    "etfcore": "core_etf",
    "etf_core": "core_etf",
    "etf-core": "core_etf",
}


def normalize_horizon(value: Any) -> str:
    horizon = str(value or "short").strip().lower()
    compact = re.sub(r"[\s/]+", "_", horizon)
    squashed = re.sub(r"[\s/_-]+", "", horizon)
    return (
        HORIZON_ALIASES.get(horizon)
        or HORIZON_ALIASES.get(compact)
        or HORIZON_ALIASES.get(squashed)
        or (horizon if horizon in BLOCK_HORIZONS else "short")
    )
