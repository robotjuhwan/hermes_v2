from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx


class BithumbAPIError(RuntimeError):
    pass


@dataclass
class BithumbConfig:
    access_key: str = ""
    secret_key: str = ""
    base_url: str = "https://api.bithumb.com"

    @property
    def ready(self) -> bool:
        return bool(self.access_key and self.secret_key)


class BithumbAdapter:
    def __init__(self, config: BithumbConfig) -> None:
        self.config = config

    async def fetch_balance_assets(self) -> list[dict[str, Any]]:
        accounts = await self._get_accounts()
        markets = self._build_krw_markets(accounts)
        prices = await self._get_market_prices(markets)
        return self._to_assets(accounts, prices)

    async def _get_accounts(self) -> list[dict[str, Any]]:
        if not self.config.ready:
            raise BithumbAPIError("bithumb config missing")

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
            raise BithumbAPIError(f"bithumb accounts request failed: {payload}")
        if not isinstance(payload, list):
            raise BithumbAPIError("bithumb accounts response malformed")
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
                raise BithumbAPIError(f"bithumb ticker request failed: {payload}")
            if not isinstance(payload, list):
                raise BithumbAPIError("bithumb ticker response malformed")

            for row in payload:
                market = str(row.get("market") or "")
                trade_price = self._to_float(row.get("trade_price"))
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
            raise BithumbAPIError(f"bithumb market list request failed: {payload}")
        if not isinstance(payload, list):
            raise BithumbAPIError("bithumb market list response malformed")

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

            balance = self._to_float(row.get("balance"))
            locked = self._to_float(row.get("locked"))
            total_qty = balance + locked
            if total_qty <= 0:
                continue

            avg_buy_price = self._to_float(row.get("avg_buy_price"))
            unit_currency = str(row.get("unit_currency") or "").upper().strip()

            if currency == "KRW":
                assets.append(
                    {
                        "asset": currency,
                        "asset_name": currency,
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

            market = f"KRW-{currency}"
            mark_price = self._to_float(prices.get(market))
            avg_price_krw = avg_buy_price if unit_currency == "KRW" else 0.0

            if mark_price <= 0 and avg_price_krw > 0:
                mark_price = avg_price_krw
            if mark_price <= 0:
                continue

            value_krw = total_qty * mark_price
            if value_krw <= 0:
                continue
            pnl_krw = (mark_price - avg_price_krw) * total_qty if avg_price_krw > 0 else 0.0

            assets.append(
                {
                    "asset": currency,
                    "asset_name": currency,
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

    def _sign_jwt(self, params: dict[str, Any] | None = None) -> str:
        if not self.config.ready:
            raise BithumbAPIError("bithumb config missing")

        header = {"alg": "HS256", "typ": "JWT"}
        payload: dict[str, Any] = {
            "access_key": self.config.access_key,
            "nonce": str(uuid4()),
            "timestamp": int(time.time() * 1000),
        }
        if params:
            query_string = urlencode(params, doseq=True).encode()
            payload["query_hash"] = hashlib.sha512(query_string).hexdigest()
            payload["query_hash_alg"] = "SHA512"

        header_b64 = self._b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = hmac.new(self.config.secret_key.encode(), signing_input, hashlib.sha256).digest()
        signature_b64 = self._b64url(signature)
        return f"{header_b64}.{payload_b64}.{signature_b64}"

    @staticmethod
    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

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

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise BithumbAPIError(f"non-json response from bithumb: {exc}") from exc
