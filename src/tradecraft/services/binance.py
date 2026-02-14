from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


class BinanceAPIError(RuntimeError):
    pass


STABLE_USD_ASSETS = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI"}


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

    async def fetch_spot_assets(self) -> list[dict[str, Any]]:
        if not self.config.spot_ready:
            raise BinanceAPIError("binance spot config missing")

        account = await self._signed_get_spot("/api/v3/account", {})
        prices = await self._get_spot_prices()
        balances = account.get("balances")
        if not isinstance(balances, list):
            raise BinanceAPIError("binance spot balances malformed")

        assets: list[dict[str, Any]] = []
        fx = float(self.config.usdt_krw_rate or 0.0)
        if fx <= 0:
            fx = 1387.0

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

            assets.append(
                {
                    "asset": symbol,
                    "asset_name": symbol,
                    "kind": "position",
                    "qty": qty,
                    "available": free,
                    "locked": locked,
                    "avg_price": 0.0,
                    "mark_price": mark_krw,
                    "value_krw": value_krw,
                    "pnl_krw": 0.0,
                }
            )

        assets.sort(key=lambda item: (item["kind"] != "cash", str(item["asset"])))
        return assets

    async def fetch_futures_assets(self) -> list[dict[str, Any]]:
        if not self.config.futures_ready:
            raise BinanceAPIError("binance futures config missing")

        account = await self._signed_get_futures("/fapi/v2/account", {})
        fx = float(self.config.usdt_krw_rate or 0.0)
        if fx <= 0:
            fx = 1387.0

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

    async def _signed_get_spot(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
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

    async def _signed_get_futures(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
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

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise BinanceAPIError(f"non-json response from binance: {exc}") from exc
