from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


class BinanceAPIError(RuntimeError):
    pass


STABLE_USD_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI"}
SPOT_COST_BASIS_CACHE_TTL_SEC = 300.0
SPOT_COST_BASIS_MIN_VALUE_KRW = 1_000.0

logger = logging.getLogger(__name__)


@dataclass
class BinanceConfig:
    spot_api_key: str = ""
    spot_api_secret: str = ""
    spot_base_url: str = "https://api.binance.com"
    futures_api_key: str = ""
    futures_api_secret: str = ""
    futures_base_url: str = "https://fapi.binance.com"
    usdt_krw_rate: float = 1387.0
    recv_window_ms: int = 5000

    @property
    def spot_ready(self) -> bool:
        return bool(self.spot_api_key and self.spot_api_secret)

    @property
    def futures_key(self) -> str:
        return self.futures_api_key or self.spot_api_key

    @property
    def futures_secret(self) -> str:
        return self.futures_api_secret or self.spot_api_secret

    @property
    def futures_ready(self) -> bool:
        return bool(self.futures_key and self.futures_secret)


class BinanceAdapter:
    def __init__(self, config: BinanceConfig) -> None:
        self.config = config
        self._spot_cost_basis_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def fetch_spot_assets(self, usdt_krw_rate: float | None = None) -> list[dict[str, Any]]:
        if not self.config.spot_ready:
            raise BinanceAPIError("binance spot config missing")

        account = await self._signed_get_spot("/api/v3/account", {})
        prices = await self._get_spot_prices()
        balances = account.get("balances")
        if not isinstance(balances, list):
            raise BinanceAPIError("binance spot balances malformed")

        assets: list[dict[str, Any]] = []
        fx = self._resolve_usdt_krw_rate(usdt_krw_rate)

        for row in balances:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("asset") or "").upper().strip()
            if not symbol:
                continue
            free = self._to_float(row.get("free"))
            locked = self._to_float(row.get("locked"))
            qty = free + locked
            if qty <= 0:
                continue

            if symbol in STABLE_USD_ASSETS:
                mark_krw = fx
                value_krw = qty * mark_krw
                assets.append(
                    {
                        "asset": symbol,
                        "asset_name": symbol,
                        "kind": "cash",
                        "qty": qty,
                        "available": free,
                        "locked": locked,
                        "avg_price": mark_krw,
                        "mark_price": mark_krw,
                        "value_krw": value_krw,
                        "pnl_krw": 0.0,
                    }
                )
                continue

            symbol_pair = f"{symbol}USDT"
            mark_usdt = self._to_float(prices.get(symbol_pair))
            if mark_usdt <= 0:
                continue
            mark_krw = mark_usdt * fx
            value_krw = qty * mark_krw
            if value_krw <= 0:
                continue
            cost_basis = (
                await self._get_spot_cost_basis(symbol_pair, qty)
                if value_krw >= SPOT_COST_BASIS_MIN_VALUE_KRW
                else {
                    "avg_price_usdt": 0.0,
                    "status": "skipped_small_position",
                    "coverage": "skipped",
                }
            )
            avg_usdt = self._to_float(cost_basis.get("avg_price_usdt"))
            avg_krw = avg_usdt * fx if avg_usdt > 0 else 0.0
            pnl_krw = (mark_usdt - avg_usdt) * qty * fx if avg_usdt > 0 else 0.0

            assets.append(
                {
                    "asset": symbol,
                    "asset_name": symbol,
                    "kind": "position",
                    "qty": qty,
                    "available": free,
                    "locked": locked,
                    "avg_price": avg_krw,
                    "mark_price": mark_krw,
                    "value_krw": value_krw,
                    "pnl_krw": pnl_krw,
                    "pnl_status": cost_basis.get("status", "unknown_cost_basis"),
                    "cost_basis_coverage": cost_basis.get("coverage", "unknown"),
                }
            )

        assets.sort(key=lambda item: (item["kind"] != "cash", str(item["asset"])))
        return assets

    async def _get_spot_cost_basis(self, symbol_pair: str, current_qty: float) -> dict[str, Any]:
        now = time.time()
        cached = self._spot_cost_basis_cache.get(symbol_pair)
        if cached and now - cached[0] <= SPOT_COST_BASIS_CACHE_TTL_SEC:
            return dict(cached[1])

        try:
            trades = await self._signed_get_spot(
                "/api/v3/myTrades",
                {"symbol": symbol_pair, "limit": 1000},
            )
        except BinanceAPIError as exc:
            logger.warning("binance spot cost-basis fetch failed for %s: %s", symbol_pair, exc)
            result = {
                "avg_price_usdt": 0.0,
                "status": "cost_basis_error",
                "coverage": "error",
                "error_message": str(exc),
            }
            self._spot_cost_basis_cache[symbol_pair] = (now, result)
            return dict(result)

        result = self._estimate_spot_cost_basis_from_trades(
            trades,
            symbol_pair=symbol_pair,
            current_qty=current_qty,
        )
        self._spot_cost_basis_cache[symbol_pair] = (now, result)
        return dict(result)

    async def fetch_spot_my_trades(
        self,
        symbol: str,
        *,
        order_id: str | int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not self.config.spot_ready:
            raise BinanceAPIError("binance spot config missing")
        params = self._trade_history_params(
            symbol=symbol,
            order_id=order_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        payload = await self._signed_get_spot("/api/v3/myTrades", params)
        if not isinstance(payload, list):
            raise BinanceAPIError("binance spot trade history malformed")
        return [dict(row) for row in payload if isinstance(row, dict)]

    async def fetch_futures_user_trades(
        self,
        symbol: str,
        *,
        order_id: str | int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")
        params = self._trade_history_params(
            symbol=symbol,
            order_id=order_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        payload = await self._signed_get_futures("/fapi/v1/userTrades", params)
        if not isinstance(payload, list):
            raise BinanceAPIError("binance futures trade history malformed")
        return [dict(row) for row in payload if isinstance(row, dict)]

    async def fetch_futures_income_history(
        self,
        symbol: str | None = None,
        *,
        income_type: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")
        params: dict[str, Any] = {"limit": max(min(int(limit), 1000), 1)}
        if symbol:
            params["symbol"] = self._normalize_symbol(symbol)
        if income_type:
            params["incomeType"] = str(income_type).upper().strip()
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        payload = await self._signed_get_futures("/fapi/v1/income", params)
        if not isinstance(payload, list):
            raise BinanceAPIError("binance futures income history malformed")
        return [dict(row) for row in payload if isinstance(row, dict)]

    def _estimate_spot_cost_basis_from_trades(
        self,
        trades: Any,
        *,
        symbol_pair: str,
        current_qty: float,
    ) -> dict[str, Any]:
        if not isinstance(trades, list):
            return {
                "avg_price_usdt": 0.0,
                "status": "unknown_cost_basis",
                "coverage": "missing_trade_history",
            }

        base_asset = symbol_pair.removesuffix("USDT")
        quote_asset = "USDT"
        position_qty = 0.0
        position_cost_usdt = 0.0
        ordered_trades = sorted(
            (row for row in trades if isinstance(row, dict)),
            key=lambda row: (
                self._to_float(row.get("time")),
                self._to_float(row.get("id")),
            ),
        )

        for row in ordered_trades:
            qty = self._to_float(row.get("qty"))
            if qty <= 0:
                continue
            price = self._to_float(row.get("price"))
            quote_qty = self._to_float(row.get("quoteQty"))
            if quote_qty <= 0 and price > 0:
                quote_qty = qty * price
            commission = self._to_float(row.get("commission"))
            commission_asset = str(row.get("commissionAsset") or "").upper().strip()
            is_buy = bool(row.get("isBuyer"))

            if is_buy:
                received_qty = qty
                quote_cost = quote_qty
                if commission_asset == base_asset:
                    received_qty = max(received_qty - commission, 0.0)
                elif commission_asset == quote_asset:
                    quote_cost += commission
                if received_qty <= 0 or quote_cost <= 0:
                    continue
                position_qty += received_qty
                position_cost_usdt += quote_cost
                continue

            sold_qty = qty
            if commission_asset == base_asset:
                sold_qty += commission
            if sold_qty <= 0 or position_qty <= 0:
                continue
            matched_qty = min(sold_qty, position_qty)
            reduction = matched_qty / position_qty
            position_cost_usdt = max(position_cost_usdt * (1.0 - reduction), 0.0)
            position_qty = max(position_qty - matched_qty, 0.0)

        if position_qty <= 0 or position_cost_usdt <= 0:
            return {
                "avg_price_usdt": 0.0,
                "status": "unknown_cost_basis",
                "coverage": "no_open_trade_basis",
            }

        avg_price_usdt = position_cost_usdt / position_qty
        tolerance = max(abs(current_qty) * 0.01, 1e-8)
        coverage = "full"
        status = "estimated_from_trade_history"
        if abs(position_qty - current_qty) > tolerance:
            coverage = "partial"
            status = "estimated_from_partial_trade_history"

        return {
            "avg_price_usdt": avg_price_usdt,
            "trade_history_qty": position_qty,
            "current_qty": current_qty,
            "status": status,
            "coverage": coverage,
        }

    async def fetch_futures_assets(self, usdt_krw_rate: float | None = None) -> list[dict[str, Any]]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")

        account = await self._signed_get_futures("/fapi/v2/account", {})
        fx = self._resolve_usdt_krw_rate(usdt_krw_rate)

        assets: list[dict[str, Any]] = []
        wallet_assets = account.get("assets")
        if isinstance(wallet_assets, list):
            for row in wallet_assets:
                if not isinstance(row, dict):
                    continue
                settle = str(row.get("asset") or "").upper().strip()
                wallet_balance = self._to_float(row.get("walletBalance"))
                available = self._to_float(row.get("availableBalance"))
                if wallet_balance <= 0:
                    continue
                if settle not in STABLE_USD_ASSETS:
                    continue

                value_krw = wallet_balance * fx
                assets.append(
                    {
                        "asset": f"{settle}-FUT",
                        "asset_name": f"{settle} Futures Wallet",
                        "kind": "cash",
                        "qty": wallet_balance,
                        "available": available,
                        "locked": max(wallet_balance - available, 0.0),
                        "avg_price": fx,
                        "mark_price": fx,
                        "value_krw": value_krw,
                        "pnl_krw": 0.0,
                    }
                )

        positions = account.get("positions")
        if isinstance(positions, list):
            for row in positions:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").upper().strip()
                pos_amt = self._to_float(row.get("positionAmt"))
                if not symbol or abs(pos_amt) <= 0:
                    continue

                mark_usdt = self._to_float(row.get("markPrice"))
                entry_usdt = self._to_float(row.get("entryPrice"))
                upnl_usdt = self._to_float(row.get("unrealizedProfit"))
                qty = abs(pos_amt)
                value_krw = qty * mark_usdt * fx
                if value_krw <= 0:
                    continue

                assets.append(
                    {
                        "asset": symbol,
                        "asset_name": f"{symbol} FUT",
                        "kind": "position",
                        "qty": qty,
                        "available": qty,
                        "locked": 0.0,
                        "avg_price": entry_usdt * fx,
                        "mark_price": mark_usdt * fx,
                        "value_krw": value_krw,
                        "pnl_krw": upnl_usdt * fx,
                    }
                )

        assets.sort(key=lambda item: (item["kind"] != "cash", str(item["asset"])))
        return assets

    async def fetch_spot_quote(self, symbol: str) -> dict[str, Any]:
        payload = await self._public_get(
            "spot",
            "/api/v3/ticker/price",
            {"symbol": self._normalize_symbol(symbol)},
        )
        return self._normalize_quote_response(payload)

    async def fetch_futures_quote(self, symbol: str) -> dict[str, Any]:
        payload = await self._public_get(
            "futures",
            "/fapi/v1/ticker/price",
            {"symbol": self._normalize_symbol(symbol)},
        )
        return self._normalize_quote_response(payload)

    async def fetch_24h_ticker(
        self,
        symbol: str,
        *,
        market: str = "spot",
    ) -> dict[str, Any]:
        market_key = self._normalize_market(market)
        path = "/api/v3/ticker/24hr" if market_key == "spot" else "/fapi/v1/ticker/24hr"
        payload = await self._public_get(
            market_key,
            path,
            {"symbol": self._normalize_symbol(symbol)},
        )
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance ticker 24hr malformed")
        return self._normalize_24h_ticker_payload(payload, symbol=symbol, market=market_key)

    async def fetch_24h_tickers(self, *, market: str = "spot") -> list[dict[str, Any]]:
        market_key = self._normalize_market(market)
        path = "/api/v3/ticker/24hr" if market_key == "spot" else "/fapi/v1/ticker/24hr"
        payload = await self._public_get(market_key, path, {})
        if not isinstance(payload, list):
            raise BinanceAPIError("binance ticker 24hr list malformed")
        return [
            self._normalize_24h_ticker_payload(row, market=market_key)
            for row in payload
            if isinstance(row, dict) and str(row.get("symbol") or "").strip()
        ]

    def _normalize_24h_ticker_payload(
        self,
        payload: dict[str, Any],
        *,
        symbol: str | None = None,
        market: str = "spot",
    ) -> dict[str, Any]:
        market_key = self._normalize_market(market)
        return {
            "symbol": self._normalize_symbol(str(payload.get("symbol") or symbol)),
            "market": market_key,
            "price": self._to_float(payload.get("lastPrice") or payload.get("price")),
            "change_pct_24h": self._to_float(payload.get("priceChangePercent")),
            "quote_volume": self._to_float(payload.get("quoteVolume")),
            "raw": payload,
        }

    async def fetch_book_ticker(
        self,
        symbol: str,
        *,
        market: str = "spot",
    ) -> dict[str, Any]:
        market_key = self._normalize_market(market)
        path = (
            "/api/v3/ticker/bookTicker"
            if market_key == "spot"
            else "/fapi/v1/ticker/bookTicker"
        )
        payload = await self._public_get(
            market_key,
            path,
            {"symbol": self._normalize_symbol(symbol)},
        )
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance book ticker malformed")
        bid = self._to_float(payload.get("bidPrice"))
        ask = self._to_float(payload.get("askPrice"))
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        return {
            "symbol": self._normalize_symbol(str(payload.get("symbol") or symbol)),
            "market": market_key,
            "bid": bid,
            "ask": ask,
            "spread_bps": ((ask - bid) / mid * 10_000.0) if mid > 0 else 0.0,
            "raw": payload,
        }

    async def fetch_klines(
        self,
        symbol: str,
        *,
        market: str = "spot",
        interval: str = "1m",
        limit: int = 120,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        market_key = self._normalize_market(market)
        path = "/api/v3/klines" if market_key == "spot" else "/fapi/v1/klines"
        params: dict[str, Any] = {
            "symbol": self._normalize_symbol(symbol),
            "interval": str(interval or "1m").strip() or "1m",
            "limit": max(min(int(limit), 1000), 1),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        payload = await self._public_get(
            market_key,
            path,
            params,
        )
        if not isinstance(payload, list):
            raise BinanceAPIError("binance klines malformed")

        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, list) or len(item) < 7:
                continue
            rows.append(
                {
                    "open_time": int(self._to_float(item[0])),
                    "open": self._to_float(item[1]),
                    "high": self._to_float(item[2]),
                    "low": self._to_float(item[3]),
                    "close": self._to_float(item[4]),
                    "volume": self._to_float(item[5]),
                    "close_time": int(self._to_float(item[6])),
                    "quote_volume": self._to_float(item[7]) if len(item) > 7 else 0.0,
                    "raw": item,
                }
            )
        return rows

    async def fetch_futures_premium_index(self, symbol: str) -> dict[str, Any]:
        payload = await self._public_get(
            "futures",
            "/fapi/v1/premiumIndex",
            {"symbol": self._normalize_symbol(symbol)},
        )
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance premium index malformed")
        return {
            "symbol": self._normalize_symbol(str(payload.get("symbol") or symbol)),
            "mark_price": self._to_float(payload.get("markPrice")),
            "index_price": self._to_float(payload.get("indexPrice")),
            "funding_rate": self._to_float(payload.get("lastFundingRate")),
            "next_funding_time": int(self._to_float(payload.get("nextFundingTime"))),
            "raw": payload,
        }

    async def fetch_futures_open_interest(self, symbol: str) -> dict[str, Any]:
        payload = await self._public_get(
            "futures",
            "/fapi/v1/openInterest",
            {"symbol": self._normalize_symbol(symbol)},
        )
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance open interest malformed")
        return {
            "symbol": self._normalize_symbol(str(payload.get("symbol") or symbol)),
            "open_interest": self._to_float(payload.get("openInterest")),
            "raw": payload,
        }

    async def fetch_spot_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": self._normalize_symbol(symbol)} if symbol else {}
        payload = await self._public_get("spot", "/api/v3/exchangeInfo", params)
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance spot exchange info malformed")
        return payload

    async def fetch_futures_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": self._normalize_symbol(symbol)} if symbol else {}
        payload = await self._public_get("futures", "/fapi/v1/exchangeInfo", params)
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance futures exchange info malformed")
        return payload

    async def fetch_exchange_filters(self, symbol: str, *, market: str = "spot") -> dict[str, dict[str, Any]]:
        market_key = self._normalize_market(market)
        if market_key == "spot":
            payload = await self.fetch_spot_exchange_info(symbol)
        else:
            payload = await self.fetch_futures_exchange_info(symbol)

        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise BinanceAPIError(f"binance {market_key} exchange symbols malformed")
        target_symbol = self._normalize_symbol(symbol)
        for row in symbols:
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper().strip() != target_symbol:
                continue
            filters = row.get("filters")
            if not isinstance(filters, list):
                raise BinanceAPIError(f"binance {market_key} exchange filters malformed")
            out: dict[str, dict[str, Any]] = {}
            for item in filters:
                if not isinstance(item, dict):
                    continue
                filter_type = str(item.get("filterType") or "").upper().strip()
                if filter_type:
                    out[filter_type] = dict(item)
            return out
        raise BinanceAPIError(f"binance {market_key} symbol not found: {target_symbol}")

    async def fetch_spot_exchange_filters(self, symbol: str) -> dict[str, dict[str, Any]]:
        return await self.fetch_exchange_filters(symbol, market="spot")

    async def fetch_futures_exchange_filters(self, symbol: str) -> dict[str, dict[str, Any]]:
        return await self.fetch_exchange_filters(symbol, market="futures")

    async def submit_spot_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        client_order_id: str,
        time_in_force: str = "IOC",
    ) -> dict[str, Any]:
        if not self.config.spot_ready:
            raise BinanceAPIError("binance spot config missing")

        params = {
            "symbol": self._normalize_symbol(symbol),
            "side": side.upper().strip(),
            "type": "LIMIT",
            "timeInForce": time_in_force.upper().strip(),
            "quantity": self._format_decimal(quantity),
            "price": self._format_decimal(limit_price),
            "newClientOrderId": client_order_id[:36],
            "newOrderRespType": "FULL",
        }
        payload = await self._signed_post_spot("/api/v3/order", params)
        return self._normalize_order_response(payload, market="spot", request_params=params)

    async def submit_futures_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        client_order_id: str,
        reduce_only: bool = False,
        time_in_force: str = "IOC",
        position_side: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")

        params = {
            "symbol": self._normalize_symbol(symbol),
            "side": side.upper().strip(),
            "type": "LIMIT",
            "timeInForce": time_in_force.upper().strip(),
            "quantity": self._format_decimal(quantity),
            "price": self._format_decimal(limit_price),
            "newClientOrderId": client_order_id[:36],
            "newOrderRespType": "RESULT",
            "reduceOnly": "true" if reduce_only else "false",
        }
        if position_side:
            params["positionSide"] = position_side.upper().strip()

        payload = await self._signed_post_futures("/fapi/v1/order", params)
        return self._normalize_order_response(
            payload,
            market="futures",
            request_params=params,
        )

    async def set_futures_margin_type(
        self,
        *,
        symbol: str,
        margin_type: str = "ISOLATED",
    ) -> dict[str, Any]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")
        return await self._signed_post_futures(
            "/fapi/v1/marginType",
            {
                "symbol": self._normalize_symbol(symbol),
                "marginType": str(margin_type or "ISOLATED").upper().strip(),
            },
        )

    async def set_futures_leverage(
        self,
        *,
        symbol: str,
        leverage: int,
    ) -> dict[str, Any]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")
        return await self._signed_post_futures(
            "/fapi/v1/leverage",
            {
                "symbol": self._normalize_symbol(symbol),
                "leverage": max(int(leverage), 1),
            },
        )

    async def fetch_futures_position_risk(self) -> list[dict[str, Any]]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")

        payload = await self._signed_get_futures("/fapi/v2/positionRisk", {})
        if not isinstance(payload, list):
            raise BinanceAPIError("binance futures position risk malformed")
        rows: list[dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "position_amt": self._to_float(row.get("positionAmt")),
                    "entry_price": self._to_float(row.get("entryPrice")),
                    "mark_price": self._to_float(row.get("markPrice")),
                    "unrealized_profit": self._to_float(
                        row.get("unRealizedProfit") or row.get("unrealizedProfit")
                    ),
                    "liquidation_price": self._to_float(row.get("liquidationPrice")),
                    "leverage": int(self._to_float(row.get("leverage"))),
                    "margin_type": str(row.get("marginType") or "").lower().strip(),
                    "raw": row,
                }
            )
        return rows

    async def fetch_open_orders(
        self,
        *,
        market: str = "spot",
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        market_key = self._normalize_market(market)
        params = {"symbol": self._normalize_symbol(symbol)} if symbol else {}
        if market_key == "spot":
            if not self.config.spot_ready:
                raise BinanceAPIError("binance spot config missing")
            payload = await self._signed_get_spot("/api/v3/openOrders", params)
        else:
            if not self.config.futures_ready:
                raise BinanceAPIError("binance futures config missing")
            payload = await self._signed_get_futures("/fapi/v1/openOrders", params)
        if not isinstance(payload, list):
            raise BinanceAPIError(f"binance {market_key} open orders malformed")
        return [
            self._normalize_order_response(row, market=market_key)
            for row in payload
            if isinstance(row, dict)
        ]

    async def _public_get(self, market: str, path: str, params: dict[str, Any]) -> Any:
        market_key = self._normalize_market(market)
        base_url = self.config.spot_base_url if market_key == "spot" else self.config.futures_base_url
        url = f"{base_url.rstrip('/')}{path}"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers={"Accept": "application/json"})
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance {market_key} public request failed: {payload}")
        return payload

    async def _get_spot_prices(self) -> dict[str, float]:
        url = f"{self.config.spot_base_url.rstrip('/')}/api/v3/ticker/price"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance spot ticker failed: {payload}")
        if not isinstance(payload, list):
            raise BinanceAPIError("binance spot ticker malformed")

        out: dict[str, float] = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            out[symbol] = self._to_float(row.get("price"))
        return out

    async def _signed_get_spot(self, path: str, params: dict[str, Any]) -> Any:
        signed = self._sign_params(
            params=params,
            secret=self.config.spot_api_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        url = f"{self.config.spot_base_url.rstrip('/')}{path}"
        headers = {
            "X-MBX-APIKEY": self.config.spot_api_key,
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=signed, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance spot request failed: {payload}")
        return payload

    async def _signed_get_futures(self, path: str, params: dict[str, Any]) -> Any:
        signed = self._sign_params(
            params=params,
            secret=self.config.futures_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        url = f"{self.config.futures_base_url.rstrip('/')}{path}"
        headers = {
            "X-MBX-APIKEY": self.config.futures_key,
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=signed, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance futures request failed: {payload}")
        return payload

    async def _signed_post_spot(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        signed = self._sign_params(
            params=params,
            secret=self.config.spot_api_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        url = f"{self.config.spot_base_url.rstrip('/')}{path}"
        headers = {
            "X-MBX-APIKEY": self.config.spot_api_key,
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, params=signed, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance spot post failed: {payload}")
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance spot post response malformed")
        return payload

    async def _signed_post_futures(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        signed = self._sign_params(
            params=params,
            secret=self.config.futures_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        url = f"{self.config.futures_base_url.rstrip('/')}{path}"
        headers = {
            "X-MBX-APIKEY": self.config.futures_key,
            "Accept": "application/json",
        }
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, params=signed, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance futures post failed: {payload}")
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance futures post response malformed")
        return payload

    @staticmethod
    def _sign_params(params: dict[str, Any], secret: str, recv_window_ms: int) -> dict[str, Any]:
        base = dict(params)
        base["timestamp"] = int(time.time() * 1000)
        base["recvWindow"] = int(recv_window_ms)
        query = urlencode(base, doseq=True)
        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        base["signature"] = signature
        return base

    @staticmethod
    def _to_float(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).replace(",", "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _resolve_usdt_krw_rate(self, override: float | None = None) -> float:
        if override is not None and override > 0:
            return float(override)
        fx = float(self.config.usdt_krw_rate or 0.0)
        if fx > 0:
            return fx
        return 1387.0

    @staticmethod
    def _format_decimal(value: float) -> str:
        text = f"{float(value):.8f}".rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _normalize_market(market: str) -> str:
        key = market.lower().strip()
        if key in {"spot", "futures"}:
            return key
        raise BinanceAPIError(f"unsupported binance market: {market}")

    @staticmethod
    def _normalize_symbol(symbol: str | None) -> str:
        out = str(symbol or "").upper().strip()
        if not out:
            raise BinanceAPIError("binance symbol missing")
        return out

    def _trade_history_params(
        self,
        *,
        symbol: str,
        order_id: str | int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": self._normalize_symbol(symbol),
            "limit": max(min(int(limit), 1000), 1),
        }
        if order_id is not None and str(order_id).strip():
            params["orderId"] = int(float(str(order_id).strip()))
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        return params

    def _normalize_quote_response(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise BinanceAPIError("binance quote malformed")
        symbol = self._normalize_symbol(str(payload.get("symbol") or ""))
        return {
            "symbol": symbol,
            "price": self._to_float(payload.get("price")),
            "raw": payload,
        }

    @staticmethod
    def _normalize_order_response(
        payload: dict[str, Any],
        *,
        market: str,
        request_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_params = request_params or {}
        backfilled_fields: list[str] = []

        def value_or_backfill(
            output_name: str,
            *payload_keys: str,
            fallback_key: str = "",
        ) -> Any:
            for key in payload_keys:
                value = payload.get(key)
                if value not in (None, ""):
                    return value
            if fallback_key:
                value = request_params.get(fallback_key)
                if value not in (None, ""):
                    backfilled_fields.append(output_name)
                    return value
            return ""

        quantity = (
            payload.get("origQty")
            or payload.get("executedQty")
            or payload.get("quantity")
            or ""
        )
        if not quantity and request_params.get("quantity") not in (None, ""):
            quantity = request_params.get("quantity")
            backfilled_fields.append("quantity")
        executed_qty = (
            payload.get("executedQty")
            or payload.get("executed_qty")
            or payload.get("cumQty")
            or ""
        )
        cum_quote = payload.get("cumQuote") or payload.get("cummulativeQuoteQty") or ""
        price = value_or_backfill("price", "price", fallback_key="price")
        return {
            "market": market,
            "symbol": str(value_or_backfill("symbol", "symbol", fallback_key="symbol")),
            "side": str(value_or_backfill("side", "side", fallback_key="side")),
            "type": str(value_or_backfill("type", "type", fallback_key="type")),
            "quantity": str(quantity),
            "executed_qty": str(executed_qty),
            "cum_quote": str(cum_quote),
            "price": str(price),
            "order_id": str(payload.get("orderId") or ""),
            "client_order_id": str(
                value_or_backfill(
                    "client_order_id",
                    "clientOrderId",
                    fallback_key="newClientOrderId",
                )
            ),
            "status": str(payload.get("status") or ""),
            "request_backfilled_fields": sorted(backfilled_fields),
            "raw": payload,
        }

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise BinanceAPIError(f"non-json response from binance: {exc}") from exc
