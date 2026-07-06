from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


@dataclass
class FxRateConfig:
    upbit_base_url: str = "https://api.upbit.com"
    bithumb_base_url: str = "https://api.bithumb.com"
    manana_base_url: str = "https://api.manana.kr"
    er_api_base_url: str = "https://open.er-api.com"
    fallback_usdt_krw: float = 1387.0
    fallback_usd_krw: float = 1387.0
    cache_ttl_sec: int = 30
    timeout_sec: float = 5.0


@dataclass
class FxRateSnapshot:
    usdt_krw: float
    usd_krw: float
    usdt_source: str
    usd_source: str
    fetched_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "usdt_krw": self.usdt_krw,
            "usd_krw": self.usd_krw,
            "usdt_source": self.usdt_source,
            "usd_source": self.usd_source,
            "fetched_at": self.fetched_at,
        }


class FxRateService:
    def __init__(self, config: FxRateConfig) -> None:
        self.config = config
        self._cached_snapshot: FxRateSnapshot | None = None
        self._cached_at = datetime.fromtimestamp(0, tz=timezone.utc)
        self._lock = asyncio.Lock()

    async def get_snapshot(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        ttl = max(int(self.config.cache_ttl_sec), 1)
        if self._cached_snapshot and now - self._cached_at <= timedelta(seconds=ttl):
            return self._cached_snapshot.to_dict()

        async with self._lock:
            now = datetime.now(timezone.utc)
            if self._cached_snapshot and now - self._cached_at <= timedelta(seconds=ttl):
                return self._cached_snapshot.to_dict()

            snapshot = await self._fetch_snapshot()
            self._cached_snapshot = snapshot
            self._cached_at = now
            return snapshot.to_dict()

    async def _fetch_snapshot(self) -> FxRateSnapshot:
        timeout = httpx.Timeout(float(self.config.timeout_sec))
        async with httpx.AsyncClient(timeout=timeout) as client:
            usdt_krw, usdt_source = await self._resolve_usdt_krw(client)
            usd_krw, usd_source = await self._resolve_usd_krw(client, usdt_krw, usdt_source)

        fetched_at = datetime.now(timezone.utc).isoformat()
        return FxRateSnapshot(
            usdt_krw=usdt_krw,
            usd_krw=usd_krw,
            usdt_source=usdt_source,
            usd_source=usd_source,
            fetched_at=fetched_at,
        )

    async def _resolve_usdt_krw(self, client: httpx.AsyncClient) -> tuple[float, str]:
        upbit = await self._fetch_upbit_usdt_krw(client)
        if upbit > 0:
            return upbit, "upbit"

        bithumb = await self._fetch_bithumb_usdt_krw(client)
        if bithumb > 0:
            return bithumb, "bithumb"

        raise RuntimeError("fx rate unavailable: USDT/KRW sources exhausted")

    async def _resolve_usd_krw(
        self,
        client: httpx.AsyncClient,
        usdt_krw: float,
        usdt_source: str,
    ) -> tuple[float, str]:
        manana = await self._fetch_manana_usd_krw(client)
        if manana > 0:
            return manana, "manana"

        er_api = await self._fetch_er_api_usd_krw(client)
        if er_api > 0:
            return er_api, "er_api"

        if usdt_krw > 0:
            return usdt_krw, f"{usdt_source}_proxy"

        raise RuntimeError("fx rate unavailable: USD/KRW sources exhausted")

    async def _fetch_upbit_usdt_krw(self, client: httpx.AsyncClient) -> float:
        try:
            url = f"{self.config.upbit_base_url.rstrip('/')}/v1/ticker"
            response = await client.get(url, params={"markets": "KRW-USDT"}, headers={"Accept": "application/json"})
            payload = response.json()
            if response.status_code >= 400:
                return 0.0
            if not isinstance(payload, list) or not payload:
                return 0.0
            first = payload[0] if isinstance(payload[0], dict) else {}
            return self._safe_positive(first.get("trade_price"))
        except Exception:
            return 0.0

    async def _fetch_bithumb_usdt_krw(self, client: httpx.AsyncClient) -> float:
        try:
            url = f"{self.config.bithumb_base_url.rstrip('/')}/public/ticker/USDT_KRW"
            response = await client.get(url, headers={"Accept": "application/json"})
            payload = response.json()
            if response.status_code >= 400:
                return 0.0
            if not isinstance(payload, dict):
                return 0.0
            if str(payload.get("status")) != "0000":
                return 0.0
            data = payload.get("data")
            if not isinstance(data, dict):
                return 0.0
            return self._safe_positive(data.get("closing_price"))
        except Exception:
            return 0.0

    async def _fetch_manana_usd_krw(self, client: httpx.AsyncClient) -> float:
        try:
            url = f"{self.config.manana_base_url.rstrip('/')}/exchange/rate/KRW/USD.json"
            response = await client.get(url, headers={"Accept": "application/json"})
            payload = response.json()
            if response.status_code >= 400:
                return 0.0
            if not isinstance(payload, list) or not payload:
                return 0.0
            first = payload[0] if isinstance(payload[0], dict) else {}
            rate = self._safe_positive(first.get("rate"))
            if rate <= 0:
                return 0.0
            if rate < 10:
                return self._safe_positive(1.0 / rate)
            return rate
        except Exception:
            return 0.0

    async def _fetch_er_api_usd_krw(self, client: httpx.AsyncClient) -> float:
        try:
            url = f"{self.config.er_api_base_url.rstrip('/')}/v6/latest/USD"
            response = await client.get(url, headers={"Accept": "application/json"})
            payload = response.json()
            if response.status_code >= 400:
                return 0.0
            if not isinstance(payload, dict):
                return 0.0
            rates = payload.get("rates")
            if not isinstance(rates, dict):
                return 0.0
            return self._safe_positive(rates.get("KRW"))
        except Exception:
            return 0.0

    @staticmethod
    def _safe_positive(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            out = float(value)
            return out if out > 0 else 0.0
        text = str(value).replace(",", "").strip()
        if not text:
            return 0.0
        try:
            out = float(text)
        except ValueError:
            return 0.0
        return out if out > 0 else 0.0
