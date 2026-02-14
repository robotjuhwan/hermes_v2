from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


class UpbitAPIError(RuntimeError):
    pass


@dataclass
class UpbitConfig:
    access_key: str = ""
    secret_key: str = ""
    base_url: str = "https://api.upbit.com"

    @property
    def ready(self) -> bool:
        return bool(self.access_key and self.secret_key)


class UpbitAdapter:
    def __init__(self, config: UpbitConfig) -> None:
        self.config = config

    async def fetch_balance_assets(self) -> list[dict[str, Any]]:
        accounts = await self._get_accounts()
        markets = self._build_krw_markets(accounts)
        prices = await self._get_market_prices(markets)
        return self._to_assets(accounts, prices)

    async def _get_accounts(self) -> list[dict[str, Any]]:
        if not self.config.ready:
            raise UpbitAPIError("upbit config missing")

        headers = {
            "Authorization": f"Bearer {self._sign_jwt()}",
            "Accept": "application/json",
        }
        url = f"{self.config.base_url.rstrip('/')}/v1/accounts"
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise UpbitAPIError(f"upbit accounts request failed: {payload}")
        if not isinstance(payload, list):
            raise UpbitAPIError("upbit accounts response malformed")
        return payload

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

    def _sign_jwt(self) -> str:
        if not self.config.ready:
            raise UpbitAPIError("upbit config missing")

        header = {"alg": "HS512", "typ": "JWT"}
        payload = {
            "access_key": self.config.access_key,
            "nonce": str(uuid4()),
        }
        header_b64 = self._b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = hmac.new(self.config.secret_key.encode(), signing_input, hashlib.sha512).digest()
        signature_b64 = self._b64url(signature)
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    @staticmethod
    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise UpbitAPIError(f"non-json response from upbit: {exc}") from exc
