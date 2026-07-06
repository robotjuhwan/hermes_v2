from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlencode
from uuid import uuid4

import httpx


logger = logging.getLogger(__name__)


class UpbitAPIError(RuntimeError):
    pass


@dataclass
class UpbitConfig:
    access_key: str = ""
    secret_key: str = ""
    base_url: str = "https://api.upbit.com"
    min_order_krw: float = 5_000.0

    @property
    def ready(self) -> bool:
        return bool(self.access_key and self.secret_key)


class UpbitAdapter:
    def __init__(self, config: UpbitConfig) -> None:
        self.config = config
        self._market_cache: tuple[float, set[str]] | None = None
        self._tick_cache: dict[str, tuple[float, float]] = {}

    async def fetch_balance_assets(self) -> list[dict[str, Any]]:
        accounts = await self._get_accounts()
        markets = self._build_krw_markets(accounts)
        prices = await self._get_market_prices(markets)
        return self._to_assets(accounts, prices)

    async def _get_accounts(self) -> list[dict[str, Any]]:
        if not self.config.ready:
            raise UpbitAPIError("upbit config missing")

        response = await self._signed_get("/v1/accounts", {})
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise UpbitAPIError(f"upbit accounts request failed: {payload}")
        if not isinstance(payload, list):
            raise UpbitAPIError("upbit accounts response malformed")
        return payload

    async def fetch_spot_quote(self, symbol: str) -> dict[str, Any]:
        market = self._to_krw_market(symbol)
        payload = await self._get_public_json("/v1/ticker", {"markets": market})
        if not isinstance(payload, list) or not payload:
            raise UpbitAPIError("upbit ticker response malformed")
        row = payload[0]
        price = self._to_float(row.get("trade_price"))
        if price <= 0:
            raise UpbitAPIError("upbit ticker price missing")
        return {
            "symbol": market,
            "market": "upbit_spot",
            "price": price,
            "last_price": price,
            "source": "upbit.ticker",
            "raw": row,
        }

    async def fetch_book_ticker(
        self,
        symbol: str,
        *,
        market: str = "upbit_spot",
    ) -> dict[str, Any]:
        del market
        pair = self._to_krw_market(symbol)
        payload = await self._get_public_json("/v1/orderbook", {"markets": pair, "count": 5})
        if not isinstance(payload, list) or not payload:
            raise UpbitAPIError("upbit orderbook response malformed")
        row = payload[0]
        units = row.get("orderbook_units")
        if not isinstance(units, list) or not units:
            raise UpbitAPIError("upbit orderbook units missing")
        first = units[0]
        bid = self._to_float(first.get("bid_price"))
        ask = self._to_float(first.get("ask_price"))
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else max(bid, ask)
        depth_krw = 0.0
        for unit in units[:5]:
            ask_price = self._to_float(unit.get("ask_price"))
            bid_price = self._to_float(unit.get("bid_price"))
            ask_size = self._to_float(unit.get("ask_size"))
            bid_size = self._to_float(unit.get("bid_size"))
            depth_krw += max(ask_price * ask_size, 0.0)
            depth_krw += max(bid_price * bid_size, 0.0)
        spread_bps = max((ask - bid) / mid * 10_000.0, 0.0) if mid > 0 and ask >= bid else 0.0
        return {
            "symbol": pair,
            "market": "upbit_spot",
            "bid": bid,
            "bid_price": bid,
            "ask": ask,
            "ask_price": ask,
            "price": mid,
            "last_price": mid,
            "spread_bps": spread_bps,
            "orderbook_depth_krw": depth_krw,
            "source": "upbit.orderbook",
            "raw": row,
        }

    async def fetch_exchange_filters(
        self,
        symbol: str,
        *,
        market: str = "upbit_spot",
    ) -> dict[str, dict[str, Any]]:
        del market
        pair = self._to_krw_market(symbol)
        tick_size = await self._fetch_tick_size(pair)
        return {
            "LOT_SIZE": {
                "minQty": "0.00000001",
                "stepSize": "0.00000001",
            },
            "PRICE_FILTER": {
                "minPrice": str(tick_size),
                "tickSize": str(tick_size),
            },
            "MIN_NOTIONAL": {
                "minNotional": str(float(self.config.min_order_krw)),
            },
        }

    async def submit_spot_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        client_order_id: str,
    ) -> dict[str, Any]:
        if not self.config.ready:
            raise UpbitAPIError("upbit config missing")
        pair = self._to_krw_market(symbol)
        side_key = "bid" if str(side or "").lower() == "buy" else "ask"
        params = {
            "market": pair,
            "side": side_key,
            "volume": self._format_decimal(quantity),
            "price": self._format_decimal(limit_price),
            "ord_type": "limit",
            "time_in_force": "ioc",
            "identifier": str(client_order_id or "")[:64],
        }
        response = await self._signed_post("/v1/orders", params)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise UpbitAPIError(f"upbit order request failed: {payload}")
        if not isinstance(payload, dict):
            raise UpbitAPIError("upbit order response malformed")
        return self._normalize_order_response(payload, market="upbit_spot")

    async def _get_market_prices(self, markets: list[str]) -> dict[str, float]:
        if not markets:
            return {}

        available = await self._get_available_markets()
        valid_markets = [market for market in markets if market in available]
        if not valid_markets:
            return {}

        out: dict[str, float] = {}
        for idx in range(0, len(valid_markets), 100):
            chunk = valid_markets[idx : idx + 100]
            url = f"{self.config.base_url.rstrip('/')}/v1/ticker"
            params = {"markets": ",".join(chunk)}
            timeout = httpx.Timeout(8.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params, headers={"Accept": "application/json"})
            payload = self._parse_json(response)
            if response.status_code >= 400:
                raise UpbitAPIError(f"upbit ticker request failed: {payload}")
            if not isinstance(payload, list):
                raise UpbitAPIError("upbit ticker response malformed")

            for row in payload:
                market = str(row.get("market") or "")
                trade_price = float(row.get("trade_price") or 0.0)
                if market:
                    out[market] = trade_price
        return out

    async def _get_available_markets(self) -> set[str]:
        now = time.time()
        if self._market_cache is not None:
            cached_at, cached = self._market_cache
            if now and now - cached_at <= 300:
                return set(cached)
        url = f"{self.config.base_url.rstrip('/')}/v1/market/all"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params={"isDetails": "false"}, headers={"Accept": "application/json"})

        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise UpbitAPIError(f"upbit market list request failed: {payload}")
        if not isinstance(payload, list):
            raise UpbitAPIError("upbit market list response malformed")

        out: set[str] = set()
        for row in payload:
            market = str(row.get("market") or "").strip()
            if market:
                out.add(market)
        if now:
            self._market_cache = (now, set(out))
        return out

    def _to_assets(self, accounts: list[dict[str, Any]], prices: dict[str, float]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for row in accounts:
            currency = str(row.get("currency") or "").upper().strip()
            if not currency:
                continue

            balance = float(row.get("balance") or 0.0)
            locked = float(row.get("locked") or 0.0)
            total_qty = balance + locked
            if total_qty <= 0:
                continue
            avg_buy_price = float(row.get("avg_buy_price") or 0.0)
            unit_currency = str(row.get("unit_currency") or "").upper().strip()

            if currency == "KRW":
                assets.append(
                    {
                        "asset": currency,
                        "symbol": currency,
                        "market": "upbit_spot",
                        "kind": "cash",
                        "qty": total_qty,
                        "available": balance,
                        "locked": locked,
                        "avg_price": 1.0,
                        "mark_price": 1.0,
                        "value_krw": total_qty,
                        "pnl_krw": 0.0,
                    }
                )
                continue

            # For now, KRW quote conversion is primary scope.
            market = f"KRW-{currency}"
            mark_price = float(prices.get(market) or 0.0)
            avg_price_krw = avg_buy_price if unit_currency == "KRW" else 0.0

            if mark_price <= 0 and avg_price_krw > 0:
                mark_price = avg_price_krw
            if mark_price <= 0:
                # Skip assets that cannot be priced in KRW yet.
                continue

            value_krw = total_qty * mark_price
            if value_krw <= 0:
                continue
            pnl_krw = (mark_price - avg_price_krw) * total_qty if avg_price_krw > 0 and mark_price > 0 else 0.0

            assets.append(
                {
                    "asset": currency,
                    "symbol": market,
                    "market": "upbit_spot",
                    "kind": "position",
                    "qty": total_qty,
                    "available": balance,
                    "locked": locked,
                    "avg_price": avg_price_krw,
                    "mark_price": mark_price,
                    "value_krw": value_krw,
                    "pnl_krw": pnl_krw,
                }
            )

        assets.sort(key=lambda item: (item["kind"] != "cash", str(item["asset"])))
        return assets

    def _build_krw_markets(self, accounts: list[dict[str, Any]]) -> list[str]:
        markets: set[str] = set()
        for row in accounts:
            currency = str(row.get("currency") or "").upper().strip()
            if not currency or currency == "KRW":
                continue
            markets.add(f"KRW-{currency}")
        return sorted(markets)

    async def _get_public_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params or {}, headers={"Accept": "application/json"})
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise UpbitAPIError(f"upbit public request failed: {payload}")
        return payload

    async def _signed_get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        headers = self._auth_headers(params)
        url = f"{self.config.base_url.rstrip('/')}{path}"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, params=params, headers=headers)

    async def _signed_post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        headers = self._auth_headers(payload)
        url = f"{self.config.base_url.rstrip('/')}{path}"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=payload, headers=headers)

    def _auth_headers(self, params: dict[str, Any] | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._sign_jwt(params or {})}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _sign_jwt(self, params: dict[str, Any] | None = None) -> str:
        if not self.config.ready:
            raise UpbitAPIError("upbit config missing")

        header = {"alg": "HS512", "typ": "JWT"}
        payload = {
            "access_key": self.config.access_key,
            "nonce": str(uuid4()),
        }
        query_string = self._query_string(params or {})
        if query_string:
            digest = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash"] = digest
            payload["query_hash_alg"] = "SHA512"
        header_b64 = self._b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = hmac.new(self.config.secret_key.encode(), signing_input, hashlib.sha512).digest()
        signature_b64 = self._b64url(signature)
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    @staticmethod
    def _query_string(params: dict[str, Any]) -> str:
        cleaned = {
            key: value
            for key, value in params.items()
            if value is not None and value != ""
        }
        if not cleaned:
            return ""
        return unquote(urlencode(cleaned, doseq=True))

    async def _fetch_tick_size(self, market: str, *, reference_price: float = 0.0) -> float:
        now = time.time()
        cached = self._tick_cache.get(market)
        if cached and now and now - cached[0] <= 300:
            return cached[1]
        try:
            payload = await self._get_public_json(
                "/v1/orderbook/instruments",
                {"markets": market},
            )
            if isinstance(payload, list) and payload:
                tick = self._to_float(payload[0].get("tick_size"))
                if tick > 0:
                    if now:
                        self._tick_cache[market] = (now, tick)
                    return tick
        except Exception:
            logger.warning(
                "upbit tick size endpoint failed for market=%s; using local tick table",
                market,
                exc_info=True,
            )
        price = max(float(reference_price or 0.0), 0.0)
        if price <= 0:
            try:
                payload = await self._get_public_json("/v1/ticker", {"markets": market})
                if isinstance(payload, list) and payload:
                    price = self._to_float(payload[0].get("trade_price"))
            except Exception:
                price = 0.0
        tick = self._krw_tick_size(price)
        if now:
            self._tick_cache[market] = (now, tick)
        return tick

    @staticmethod
    def _to_krw_market(symbol: str) -> str:
        text = str(symbol or "").upper().strip()
        if text.startswith("KRW-"):
            return text
        if "-" in text:
            quote, base = text.split("-", 1)
            if quote == "KRW" and base:
                return text
        if text.endswith("USDT"):
            text = text.removesuffix("USDT")
        elif text.endswith("KRW"):
            text = text.removesuffix("KRW")
        return f"KRW-{text}"

    @staticmethod
    def _krw_tick_size(price: float) -> float:
        if price >= 2_000_000:
            return 1_000.0
        if price >= 1_000_000:
            return 500.0
        if price >= 500_000:
            return 100.0
        if price >= 100_000:
            return 50.0
        if price >= 10_000:
            return 10.0
        if price >= 1_000:
            return 1.0
        if price >= 100:
            return 0.1
        if price >= 10:
            return 0.01
        if price >= 1:
            return 0.001
        if price >= 0.1:
            return 0.0001
        if price >= 0.01:
            return 0.00001
        if price >= 0.001:
            return 0.000001
        if price >= 0.0001:
            return 0.0000001
        return 0.00000001

    @staticmethod
    def _normalize_order_response(payload: dict[str, Any], *, market: str) -> dict[str, Any]:
        state = str(payload.get("state") or "").lower()
        remaining = UpbitAdapter._to_float(payload.get("remaining_volume"))
        executed = UpbitAdapter._to_float(payload.get("executed_volume"))
        requested = UpbitAdapter._to_float(payload.get("volume"))
        if state == "done":
            status = "FILLED"
        elif state == "cancel" and executed > 0:
            status = "PARTIALLY_FILLED"
        elif state == "cancel":
            status = "EXPIRED"
        elif state in {"wait", "watch"}:
            status = "NEW"
        else:
            status = state.upper() or "UNKNOWN"
        return {
            "market": market,
            "symbol": str(payload.get("market") or ""),
            "order_id": str(payload.get("uuid") or ""),
            "client_order_id": str(payload.get("identifier") or ""),
            "status": status,
            "executed_qty": executed,
            "executedQty": executed,
            "remaining_qty": remaining,
            "orig_qty": requested,
            "price": UpbitAdapter._to_float(payload.get("price")),
            "raw": payload,
        }

    @staticmethod
    def _format_decimal(value: Any) -> str:
        number = UpbitAdapter._to_float(value)
        return f"{number:.12f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(str(value or "0").replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise UpbitAPIError(f"non-json response from upbit: {exc}") from exc
