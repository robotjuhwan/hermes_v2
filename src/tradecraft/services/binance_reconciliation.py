from __future__ import annotations

from typing import Any

from tradecraft.services.binance_symbol import (
    UPBIT_SPOT_MARKET,
    is_upbit_market,
    normalize_market,
    normalize_position_side,
    upbit_market_symbol,
)

STABLE_SPOT_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI"}
ALLOCATION_STATUSES = {"entry_pending", "open", "exit_pending", "paused"}


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def spot_position_assets(account: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[Any] = []
    if isinstance(account.get("spot_assets"), list):
        rows.extend(account.get("spot_assets") or [])
    if isinstance(account.get("positions"), list):
        rows.extend(
            row
            for row in account.get("positions") or []
            if isinstance(row, dict)
            and normalize_market(row.get("market") or "spot") == "spot"
        )

    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset") or "").upper().strip()
        raw_symbol = str(row.get("symbol") or "").upper().strip()
        if not asset and raw_symbol.endswith("USDT"):
            asset = raw_symbol.removesuffix("USDT")
        if not asset or asset in STABLE_SPOT_ASSETS:
            continue
        kind = str(row.get("kind") or "position").lower()
        if kind == "cash":
            continue
        symbol = raw_symbol or f"{asset}USDT"
        qty = _safe_float(row.get("qty") or row.get("quantity") or row.get("balance"))
        available = _safe_float(row.get("available") or row.get("free"))
        locked = _safe_float(row.get("locked"))
        if qty <= 0 and available + locked > 0:
            qty = available + locked
        if qty <= 0:
            continue
        key = f"{asset}:{symbol}"
        if key in seen:
            continue
        seen.add(key)
        assets.append(
            {
                **row,
                "asset": asset,
                "symbol": symbol,
                "qty": qty,
                "available": available,
                "locked": locked,
            }
        )
    return assets


def upbit_position_assets(account: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[Any] = []
    if isinstance(account.get("upbit_spot_assets"), list):
        rows.extend(account.get("upbit_spot_assets") or [])
    if isinstance(account.get("positions"), list):
        rows.extend(
            row
            for row in account.get("positions") or []
            if isinstance(row, dict)
            and normalize_market(row.get("market") or "") == UPBIT_SPOT_MARKET
        )

    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset") or "").upper().strip()
        raw_symbol = str(row.get("symbol") or "").upper().strip()
        if not asset and raw_symbol.startswith("KRW-"):
            asset = raw_symbol.split("-", 1)[1]
        if not asset or asset == "KRW":
            continue
        kind = str(row.get("kind") or "position").lower()
        if kind == "cash":
            continue
        symbol = raw_symbol or upbit_market_symbol(asset)
        qty = _safe_float(row.get("qty") or row.get("quantity") or row.get("balance"))
        available = _safe_float(row.get("available") or row.get("free"))
        locked = _safe_float(row.get("locked"))
        if qty <= 0 and available + locked > 0:
            qty = available + locked
        if qty <= 0:
            continue
        key = f"{asset}:{symbol}"
        if key in seen:
            continue
        seen.add(key)
        assets.append(
            {
                **row,
                "asset": asset,
                "symbol": symbol,
                "market": UPBIT_SPOT_MARKET,
                "qty": qty,
                "available": available,
                "locked": locked,
            }
        )
    return assets


def position_assets_for_market(
    account: dict[str, Any],
    *,
    market: str,
) -> list[dict[str, Any]]:
    return (
        upbit_position_assets(account)
        if is_upbit_market(market)
        else spot_position_assets(account)
    )


def allocated_qty_by_symbol(
    blocks: list[dict[str, Any]],
    *,
    market: str,
    active_statuses: set[str] | None = None,
) -> dict[str, float]:
    statuses = active_statuses or ALLOCATION_STATUSES
    allocated: dict[str, float] = {}
    normalized_market = normalize_market(market)
    for block in blocks:
        if normalize_market(block.get("market")) != normalized_market:
            continue
        if normalize_position_side(block.get("side")) != "long":
            continue
        if str(block.get("status") or "") not in statuses:
            continue
        symbol = str(block.get("symbol") or "").upper()
        if not symbol:
            continue
        qty = _safe_float(block.get("qty_open") or block.get("qty_initial"))
        allocated[symbol] = allocated.get(symbol, 0.0) + max(qty, 0.0)
    return allocated
