from __future__ import annotations

from typing import Any, Callable

from tradecraft.services.binance_ledger import safe_float
from tradecraft.services.binance_symbol import UPBIT_SPOT_MARKET, normalize_market

QUOTE_ASSET_SUFFIXES = (
    "USDT",
    "USDC",
    "FDUSD",
    "BUSD",
    "TUSD",
    "USD",
    "KRW",
    "BTC",
    "ETH",
    "BNB",
)
USDT_EQUIVALENT_ASSETS = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD"}
FEE_CONVERSION_QUOTE_SYMBOLS = {"BNB": "BNBUSDT"}


def symbol_base_quote(symbol: Any, *, market: str = "spot") -> tuple[str, str]:
    text = str(symbol or "").upper().strip()
    normalized_market = normalize_market(market)
    if normalized_market == UPBIT_SPOT_MARKET and text.startswith("KRW-"):
        return text.split("-", 1)[1], "KRW"
    if "-" in text:
        quote, _, base = text.partition("-")
        if quote and base:
            return base, quote
    for suffix in QUOTE_ASSET_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)], suffix
    return text, "USDT"


def iter_payload_dicts(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            rows.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return rows


def first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = safe_float(row.get(key))
        if value != 0:
            return value
    return 0.0


def asset_amount_to_usdt(
    *,
    amount: float,
    asset: str,
    base_asset: str,
    quote_asset: str,
    price: float,
    upbit_usdt_krw_rate: float,
    conversion_price_provider: Callable[[str], float] | None,
    unconverted: list[dict[str, Any]],
) -> float:
    qty = safe_float(amount)
    if qty == 0:
        return 0.0
    normalized_asset = str(asset or "").upper().strip()
    if not normalized_asset or normalized_asset in USDT_EQUIVALENT_ASSETS:
        return qty
    if normalized_asset == "KRW":
        return qty / max(safe_float(upbit_usdt_krw_rate), 1.0)
    if normalized_asset == base_asset and price > 0:
        notional = qty * price
        if quote_asset == "KRW":
            return notional / max(safe_float(upbit_usdt_krw_rate), 1.0)
        if quote_asset in USDT_EQUIVALENT_ASSETS:
            return notional
    conversion_price = 0.0
    if conversion_price_provider is not None:
        conversion_price = safe_float(conversion_price_provider(normalized_asset))
    if conversion_price > 0:
        return qty * conversion_price
    unconverted.append(
        {
            "asset": normalized_asset,
            "amount": qty,
            "reason": "missing_conversion_price",
        }
    )
    return 0.0
