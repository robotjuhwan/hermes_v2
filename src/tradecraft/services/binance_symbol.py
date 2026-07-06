from __future__ import annotations

import re
from typing import Any

UPBIT_SPOT_MARKET = "upbit_spot"
ALLOWED_MARKETS = {"spot", "futures", UPBIT_SPOT_MARKET}


def normalize_market(value: Any) -> str:
    market = str(value or "spot").strip().lower()
    compact = re.sub(r"[\s/:-]+", "_", market)
    if market in {"upbit", "upbit-spot", "krw_spot", "krw-spot"} or compact in {
        "upbit",
        "upbit_spot",
        "krw_spot",
    }:
        market = UPBIT_SPOT_MARKET
    elif compact in {
        "binance_futures",
        "binance_future",
        "binance_perp",
        "binance_perpetual",
        "binance_futures_account",
        "binance_futures_wallet",
        "futures_account",
        "futures_wallet",
        "usdm_futures",
        "um_futures",
    }:
        market = "futures"
    elif compact in {"binance_spot", "spot_account", "spot_wallet"}:
        market = "spot"
    return market if market in ALLOWED_MARKETS else "spot"


def explicit_market_scope(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = normalize_market(raw)
    compact = re.sub(r"[\s/:-]+", "_", raw.lower())
    if normalized in {"futures", UPBIT_SPOT_MARKET}:
        return normalized
    if compact in {
        "spot",
        "binance_spot",
        "spot_account",
        "spot_wallet",
    }:
        return "spot"
    return ""


def is_upbit_market(value: Any) -> bool:
    return normalize_market(value) == UPBIT_SPOT_MARKET


def upbit_market_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    if not symbol:
        return ""
    if symbol.startswith("KRW-"):
        return symbol
    if symbol.endswith("USDT"):
        symbol = symbol.removesuffix("USDT")
    elif symbol.endswith("KRW"):
        symbol = symbol.removesuffix("KRW")
    if "-" in symbol:
        quote, _, base = symbol.partition("-")
        if quote == "KRW" and base:
            return f"KRW-{base}"
        symbol = base or symbol
    return f"KRW-{symbol}"


def upbit_market_to_usdt_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    if symbol.startswith("KRW-"):
        return f"{symbol.split('-', 1)[1]}USDT"
    if symbol.endswith("KRW"):
        return f"{symbol.removesuffix('KRW')}USDT"
    return symbol


def normalize_position_side(value: Any) -> str:
    side = str(value or "long").strip().lower()
    return "short" if side in {"short", "sell"} else "long"
