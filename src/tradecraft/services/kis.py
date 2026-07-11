from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx


class KISAPIError(RuntimeError):
    pass


def _positive_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return max(int(float(value)), 0)
        text = str(value).replace(",", "").strip()
        if not text or text in {"-", "N/A", "nan"}:
            return 0
        return max(int(float(text)), 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class KISConfig:
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    product_code: str = ""
    base_url: str = "https://openapi.koreainvestment.com:9443"
    tr_id_balance: str = "TTTC8434R"
    tr_id_us_present_balance: str = "CTRP6504R"
    tr_id_quote: str = "FHKST01010100"
    tr_id_daily_chart: str = "FHKST03010100"
    tr_id_order_buy: str = "TTTC0012U"
    tr_id_order_sell: str = "TTTC0011U"
    tr_id_order_revise_cancel: str = "TTTC0013U"
    tr_id_order_daily: str = "TTTC0081R"
    tr_id_order_cancelable: str = "TTTC0084R"
    exchange_id: str = "KRX"
    cust_type: str = "P"
    rate_limit_enabled: bool = True
    rest_rate_limit_per_sec: float = 8.0
    account_min_interval_sec: float = 8.0
    token_min_interval_sec: float = 65.0
    rate_limit_db_path: str = ".runtime/kis_rate_limit.db"

    @property
    def ready(self) -> bool:
        return bool(
            self.app_key and self.app_secret and self.account_no and self.product_code
        )


class KISSharedRateLimiter:
    def __init__(
        self,
        *,
        db_path: str | Path,
        rest_rate_limit_per_sec: float,
        token_min_interval_sec: float,
        account_min_interval_sec: float = 1.0,
        enabled: bool = True,
    ) -> None:
        self.path = Path(db_path)
        self.rest_rate_limit_per_sec = max(float(rest_rate_limit_per_sec), 0.0)
        self.account_min_interval_sec = max(float(account_min_interval_sec), 0.0)
        self.token_min_interval_sec = max(float(token_min_interval_sec), 0.0)
        self.enabled = bool(enabled)
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def wait(self, bucket: str = "rest") -> None:
        if not self.enabled:
            return
        await self._ensure_schema()
        await asyncio.to_thread(self._wait_sync, bucket)

    async def penalize(self, bucket: str = "rest", *, delay_sec: float) -> None:
        if not self.enabled:
            return
        delay = max(float(delay_sec or 0.0), 0.0)
        if delay <= 0:
            return
        await self._ensure_schema()
        await asyncio.to_thread(self._penalize_sync, bucket, delay)

    async def get_token(self, token_key: str) -> tuple[str, datetime] | None:
        if not self.enabled:
            return None
        await self._ensure_schema()
        return await asyncio.to_thread(self._get_token_sync, token_key)

    async def save_token(
        self,
        *,
        token_key: str,
        access_token: str,
        expires_at: datetime,
    ) -> None:
        if not self.enabled:
            return
        await self._ensure_schema()
        await asyncio.to_thread(
            self._save_token_sync,
            token_key,
            access_token,
            expires_at.astimezone(timezone.utc).timestamp(),
        )

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            await asyncio.to_thread(self._ensure_schema_sync)
            self._ready = True

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.path), timeout=30.0)

    def _ensure_schema_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rate_limits (
                    bucket TEXT PRIMARY KEY,
                    next_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS access_tokens (
                    token_key TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _wait_sync(self, bucket: str) -> None:
        name = self._bucket_name(bucket)
        interval = self._interval_for_bucket(name)
        while True:
            now = time.time()
            with self._connect() as conn:
                conn.isolation_level = None
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT next_at FROM rate_limits WHERE bucket = ?",
                    (name,),
                ).fetchone()
                next_at = float(row[0]) if row else 0.0
                wait_for = max(next_at - now, 0.0)
                if wait_for <= 0:
                    if interval > 0:
                        conn.execute(
                            """
                            INSERT INTO rate_limits(bucket, next_at)
                            VALUES(?, ?)
                            ON CONFLICT(bucket) DO UPDATE SET next_at = excluded.next_at
                            """,
                            (name, now + interval),
                        )
                    conn.execute("COMMIT")
                    return
                conn.execute("ROLLBACK")
            time.sleep(min(wait_for, 1.0))

    def _penalize_sync(self, bucket: str, delay_sec: float) -> None:
        name = self._bucket_name(bucket)
        next_at = time.time() + max(float(delay_sec or 0.0), 0.0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits(bucket, next_at)
                VALUES(?, ?)
                ON CONFLICT(bucket) DO UPDATE SET
                    next_at = max(rate_limits.next_at, excluded.next_at)
                """,
                (name, next_at),
            )

    @staticmethod
    def _bucket_name(bucket: str) -> str:
        raw_name = str(bucket or "").strip().lower()
        if raw_name == "token":
            return "token"
        if raw_name.startswith("account:") or raw_name.startswith("balance:"):
            return raw_name
        if raw_name in {"account", "balance"}:
            return "account"
        return "rest"

    @staticmethod
    def _is_account_bucket(bucket: str) -> bool:
        raw_name = KISSharedRateLimiter._bucket_name(bucket)
        return raw_name == "account" or raw_name.startswith(("account:", "balance:"))

    def _interval_for_bucket(self, bucket: str) -> float:
        if bucket == "token":
            return self.token_min_interval_sec
        if self._is_account_bucket(bucket):
            rest_interval = 0.0
            if self.rest_rate_limit_per_sec > 0:
                rest_interval = 1.0 / self.rest_rate_limit_per_sec
            return max(rest_interval, self.account_min_interval_sec)
        if self.rest_rate_limit_per_sec <= 0:
            return 0.0
        return 1.0 / self.rest_rate_limit_per_sec

    def _get_token_sync(self, token_key: str) -> tuple[str, datetime] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT access_token, expires_at
                FROM access_tokens
                WHERE token_key = ?
                LIMIT 1
                """,
                (str(token_key or ""),),
            ).fetchone()
        if not row:
            return None
        token = str(row[0] or "").strip()
        expires_at = datetime.fromtimestamp(float(row[1] or 0.0), tz=timezone.utc)
        if not token:
            return None
        return token, expires_at

    def _save_token_sync(
        self,
        token_key: str,
        access_token: str,
        expires_at: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO access_tokens(token_key, access_token, expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(token_key) DO UPDATE SET
                    access_token = excluded.access_token,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (str(token_key or ""), str(access_token or ""), float(expires_at), time.time()),
            )


class KISAdapter:
    def __init__(self, config: KISConfig) -> None:
        self.config = config
        self._access_token = ""
        self._token_expiry = datetime.fromtimestamp(0, tz=timezone.utc)
        self._token_lock = asyncio.Lock()
        self._rate_limiter = KISSharedRateLimiter(
            db_path=config.rate_limit_db_path,
            rest_rate_limit_per_sec=config.rest_rate_limit_per_sec,
            account_min_interval_sec=config.account_min_interval_sec,
            token_min_interval_sec=config.token_min_interval_sec,
            enabled=config.rate_limit_enabled,
        )
        self._token_key = hashlib.sha256(
            f"{config.base_url.rstrip('/')}|{config.app_key}".encode("utf-8")
        ).hexdigest()

    def _account_bucket(self) -> str:
        return f"account:{self._token_key}"

    async def fetch_balance_assets(self) -> list[dict[str, Any]]:
        rows, summary = await self._fetch_balance_rows()
        return self._to_assets(rows, summary)

    async def fetch_us_balance_assets(
        self, usd_krw_rate: float | None = None
    ) -> list[dict[str, Any]]:
        rows, summary = await self._fetch_us_balance_rows()
        return self._to_us_assets(rows, summary, usd_krw_rate=usd_krw_rate)

    async def fetch_domestic_quote(self, symbol: str) -> dict[str, Any]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        code = str(symbol or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise KISAPIError("invalid domestic symbol")

        token = await self._get_access_token()
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_quote,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
        }
        url = f"{self.config.base_url.rstrip('/')}/uapi/domestic-stock/v1/quotations/inquire-price"
        response, payload = await self._request_json(
            "get",
            url,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis quote request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis quote request rejected: {msg}")

        output = payload.get("output")
        if not isinstance(output, dict):
            raise KISAPIError("kis quote malformed response")

        price = self._to_float(output.get("stck_prpr"))
        if price <= 0:
            price = self._to_float(output.get("askp1"))
        if price <= 0:
            raise KISAPIError("kis quote has no valid price")

        return {
            "symbol": code,
            "name": str(output.get("hts_kor_isnm") or code),
            "price": price,
            "raw": output,
        }

    async def fetch_domestic_daily_prices(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        adjusted: bool = True,
    ) -> list[dict[str, Any]]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        code = str(symbol or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise KISAPIError("invalid domestic symbol")
        start = str(start_date or "").strip()
        end = str(end_date or "").strip()
        if any(len(value) != 8 or not value.isdigit() for value in (start, end)):
            raise KISAPIError("invalid domestic chart date")

        token = await self._get_access_token()
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_daily_chart,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
        }
        url = (
            f"{self.config.base_url.rstrip('/')}"
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        )
        response, payload = await self._request_json(
            "get",
            url,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis daily chart request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            message = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis daily chart request rejected: {message}")

        output = payload.get("output2")
        if not isinstance(output, list):
            raise KISAPIError("kis daily chart malformed response")
        rows: list[dict[str, Any]] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            raw_date = str(item.get("stck_bsop_date") or "").strip()
            if len(raw_date) != 8 or not raw_date.isdigit():
                continue
            rows.append(
                {
                    "open_time": (
                        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                    ),
                    "open": self._to_float(item.get("stck_oprc")),
                    "high": self._to_float(item.get("stck_hgpr")),
                    "low": self._to_float(item.get("stck_lwpr")),
                    "close": self._to_float(item.get("stck_clpr")),
                    "volume": self._to_float(item.get("acml_vol")),
                }
            )
        return rows

    async def submit_domestic_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: int = 0,
        order_type: str = "01",
    ) -> dict[str, Any]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        code = str(symbol or "").strip()
        if len(code) != 6 or not code.isdigit():
            raise KISAPIError("invalid domestic symbol")
        qty = int(quantity)
        if qty <= 0:
            raise KISAPIError("quantity must be positive")

        norm_side = str(side or "").strip().lower()
        if norm_side not in {"buy", "sell"}:
            raise KISAPIError("side must be buy or sell")

        cano, product_code = self._account_parts()
        payload = {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "PDNO": code,
            "ORD_DVSN": str(order_type or "01"),
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(max(int(price), 0)),
            "EXCG_ID_DVSN_CD": self.config.exchange_id or "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": "",
        }
        url = f"{self.config.base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/order-cash"
        body: dict[str, Any] = {}
        for attempt in range(2):
            token = await self._get_access_token(force_refresh=attempt > 0)
            headers = {
                "Authorization": f"Bearer {token}",
                "appkey": self.config.app_key,
                "appsecret": self.config.app_secret,
                "tr_id": (
                    self.config.tr_id_order_buy
                    if norm_side == "buy"
                    else self.config.tr_id_order_sell
                ),
                "custtype": self.config.cust_type,
                "Accept": "application/json",
                "Content-Type": "application/json; charset=UTF-8",
            }
            response, body = await self._request_json(
                "post",
                url,
                content=json.dumps(payload),
                headers=headers,
                timeout=httpx.Timeout(10.0),
            )
            if self._is_access_token_expired_payload(body) and attempt == 0:
                self._clear_access_token()
                continue
            if response.status_code >= 400:
                raise KISAPIError(f"kis order request failed: {body}")
            if str(body.get("rt_cd")) != "0":
                msg = str(body.get("msg1") or body.get("msg_cd") or body)
                raise KISAPIError(f"kis order request rejected: {msg}")
            break
        else:
            raise KISAPIError(f"kis order request failed after token refresh: {body}")

        out = body.get("output")
        output = out if isinstance(out, dict) else {}
        order_orgno = self._first_text(
            output,
            "KRX_FWDG_ORD_ORGNO",
            "krx_fwdg_ord_orgno",
            "ORD_GNO_BRNO",
            "ord_gno_brno",
            "ORD_ORGNO",
            "ord_orgno",
        )
        return {
            "symbol": code,
            "side": norm_side,
            "quantity": qty,
            "price": int(max(price, 0)),
            "order_type": str(order_type or "01"),
            "order_no": str(output.get("ODNO") or output.get("odno") or ""),
            "order_orgno": order_orgno,
            "response": body,
        }

    async def fetch_domestic_order_daily(
        self,
        *,
        symbol: str = "",
        order_no: str = "",
        order_orgno: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        side_code: str = "00",
        ccld_dvsn: str = "00",
        max_pages: int = 3,
    ) -> dict[str, Any]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        resolved_start = self._normalize_yyyymmdd(start_date) or today
        resolved_end = self._normalize_yyyymmdd(end_date) or today
        code = str(symbol or "").strip()
        if code and (len(code) != 6 or not code.isdigit()):
            raise KISAPIError("invalid domestic symbol")

        rows: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        fk100 = ""
        nk100 = ""
        tr_cont = ""
        page_limit = max(int(max_pages), 1)
        for _ in range(page_limit):
            payload, next_tr_cont = await self._inquire_domestic_order_daily_page(
                symbol=code,
                order_no=str(order_no or "").strip(),
                order_orgno=str(order_orgno or "").strip(),
                start_date=resolved_start,
                end_date=resolved_end,
                side_code=str(side_code or "00"),
                ccld_dvsn=str(ccld_dvsn or "00"),
                fk100=fk100,
                nk100=nk100,
                tr_cont=tr_cont,
            )
            output1 = payload.get("output1")
            if isinstance(output1, list):
                rows.extend(item for item in output1 if isinstance(item, dict))
            elif isinstance(output1, dict):
                rows.append(output1)

            output2 = payload.get("output2")
            if isinstance(output2, list):
                summaries.extend(item for item in output2 if isinstance(item, dict))
            elif isinstance(output2, dict):
                summaries.append(output2)

            fk100 = str(payload.get("ctx_area_fk100") or "")
            nk100 = str(payload.get("ctx_area_nk100") or "")
            if next_tr_cont not in {"M", "F"}:
                break
            if not (fk100 or nk100):
                break
            tr_cont = "N"

        return {
            "status": "ok",
            "rows": rows,
            "orders": [self.normalize_domestic_order_row(row) for row in rows],
            "summary": summaries[-1] if summaries else {},
            "start_date": resolved_start,
            "end_date": resolved_end,
            "symbol": code,
            "order_no": str(order_no or "").strip(),
        }

    async def fetch_domestic_cancelable_orders(
        self,
        *,
        query_type: str = "0",
        side_code: str = "0",
        max_pages: int = 3,
    ) -> dict[str, Any]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        rows: list[dict[str, Any]] = []
        fk100 = ""
        nk100 = ""
        tr_cont = ""
        page_limit = max(int(max_pages), 1)
        for _ in range(page_limit):
            payload, next_tr_cont = await self._inquire_domestic_cancelable_page(
                query_type=str(query_type or "0"),
                side_code=str(side_code or "0"),
                fk100=fk100,
                nk100=nk100,
                tr_cont=tr_cont,
            )
            output = payload.get("output")
            if isinstance(output, list):
                rows.extend(item for item in output if isinstance(item, dict))
            elif isinstance(output, dict):
                rows.append(output)

            fk100 = str(payload.get("ctx_area_fk100") or "")
            nk100 = str(payload.get("ctx_area_nk100") or "")
            if next_tr_cont not in {"M", "F"}:
                break
            if not (fk100 or nk100):
                break
            tr_cont = "N"

        return {
            "status": "ok",
            "rows": rows,
            "orders": [self.normalize_domestic_order_row(row) for row in rows],
        }

    async def cancel_domestic_order(
        self,
        *,
        order_no: str,
        order_orgno: str = "",
        quantity: int = 0,
        order_type: str = "00",
        exchange_id: str = "",
    ) -> dict[str, Any]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        resolved_order_no = str(order_no or "").strip()
        if not resolved_order_no:
            raise KISAPIError("order_no is required")

        cancelable_match: dict[str, Any] = {}
        cancelable_lookup_error: Exception | None = None
        try:
            cancelable = await self.fetch_domestic_cancelable_orders(max_pages=3)
            for row in cancelable.get("orders") or []:
                if str(row.get("order_no") or "").strip() == resolved_order_no:
                    cancelable_match = row if isinstance(row, dict) else {}
                    break
        except Exception as exc:
            cancelable_lookup_error = exc
            cancelable_match = {}

        resolved_orgno = str(order_orgno or "").strip() or str(
            cancelable_match.get("order_orgno") or ""
        ).strip()
        if not resolved_orgno:
            if cancelable_lookup_error is not None:
                raise KISAPIError(
                    "cancelable order lookup failed before resolving order_orgno: "
                    f"{cancelable_lookup_error}"
                ) from cancelable_lookup_error
            raise KISAPIError("cancelable order orgno not found")

        remaining_qty = _positive_int(cancelable_match.get("cancelable_qty"))
        if remaining_qty <= 0:
            remaining_qty = _positive_int(cancelable_match.get("remaining_qty"))
        requested_qty = int(quantity or 0)
        if requested_qty <= 0:
            requested_qty = remaining_qty

        token = await self._get_access_token()
        cano, product_code = self._account_parts()
        payload = {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "KRX_FWDG_ORD_ORGNO": resolved_orgno,
            "ORGN_ODNO": resolved_order_no,
            "ORD_DVSN": str(order_type or "00"),
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(max(int(requested_qty), 0)),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
            "EXCG_ID_DVSN_CD": str(exchange_id or self.config.exchange_id or "KRX"),
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_order_revise_cancel,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
        }

        url = f"{self.config.base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        response, body = await self._request_json(
            "post",
            url,
            content=json.dumps(payload),
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis cancel request failed: {body}")
        if str(body.get("rt_cd")) != "0":
            msg = str(body.get("msg1") or body.get("msg_cd") or body)
            raise KISAPIError(f"kis cancel request rejected: {msg}")

        out = body.get("output")
        output = out if isinstance(out, dict) else {}
        return {
            "order_no": resolved_order_no,
            "order_orgno": resolved_orgno,
            "cancel_order_no": str(output.get("ODNO") or output.get("odno") or ""),
            "cancel_order_orgno": self._first_text(
                output,
                "KRX_FWDG_ORD_ORGNO",
                "krx_fwdg_ord_orgno",
                "ORD_GNO_BRNO",
                "ord_gno_brno",
                "ORD_ORGNO",
                "ord_orgno",
            ),
            "quantity": requested_qty,
            "response": body,
        }

    async def _inquire_domestic_order_daily_page(
        self,
        *,
        symbol: str,
        order_no: str,
        order_orgno: str,
        start_date: str,
        end_date: str,
        side_code: str,
        ccld_dvsn: str,
        fk100: str = "",
        nk100: str = "",
        tr_cont: str = "",
    ) -> tuple[dict[str, Any], str]:
        token = await self._get_access_token()
        cano, product_code = self._account_parts()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": start_date,
            "INQR_END_DT": end_date,
            "SLL_BUY_DVSN_CD": side_code,
            "PDNO": symbol,
            "CCLD_DVSN": ccld_dvsn,
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": order_orgno,
            "ODNO": order_no,
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": fk100,
            "CTX_AREA_NK100": nk100,
            "EXCG_ID_DVSN_CD": self.config.exchange_id or "KRX",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_order_daily,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont

        url = f"{self.config.base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        response, payload = await self._request_json(
            "get",
            url,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis order inquiry request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis order inquiry request rejected: {msg}")
        next_tr_cont = str(response.headers.get("tr_cont") or "").upper().strip()
        return payload, next_tr_cont

    async def _inquire_domestic_cancelable_page(
        self,
        *,
        query_type: str,
        side_code: str,
        fk100: str = "",
        nk100: str = "",
        tr_cont: str = "",
    ) -> tuple[dict[str, Any], str]:
        token = await self._get_access_token()
        cano, product_code = self._account_parts()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "INQR_DVSN_1": query_type,
            "INQR_DVSN_2": side_code,
            "CTX_AREA_FK100": fk100,
            "CTX_AREA_NK100": nk100,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_order_cancelable,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont

        url = f"{self.config.base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
        response, payload = await self._request_json(
            "get",
            url,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis cancelable inquiry request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis cancelable inquiry request rejected: {msg}")
        next_tr_cont = str(response.headers.get("tr_cont") or "").upper().strip()
        return payload, next_tr_cont

    async def _fetch_balance_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        all_rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}

        fk100 = ""
        nk100 = ""
        tr_cont = ""
        for _ in range(12):
            payload, next_tr_cont = await self._inquire_balance_page(
                fk100=fk100,
                nk100=nk100,
                tr_cont=tr_cont,
            )

            output1 = payload.get("output1")
            if isinstance(output1, list):
                all_rows.extend(item for item in output1 if isinstance(item, dict))
            elif isinstance(output1, dict):
                all_rows.append(output1)

            output2 = payload.get("output2")
            if isinstance(output2, list) and output2:
                if isinstance(output2[0], dict):
                    summary = output2[0]
            elif isinstance(output2, dict):
                summary = output2

            fk100 = str(payload.get("ctx_area_fk100") or "")
            nk100 = str(payload.get("ctx_area_nk100") or "")

            if next_tr_cont not in {"M", "F"}:
                break
            if not (fk100 or nk100):
                break
            tr_cont = "N"

        return all_rows, summary

    async def _inquire_balance_page(
        self,
        fk100: str = "",
        nk100: str = "",
        tr_cont: str = "",
    ) -> tuple[dict[str, Any], str]:
        token = await self._get_access_token()
        cano, product_code = self._account_parts()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": fk100,
            "CTX_AREA_NK100": nk100,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_balance,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont

        url = f"{self.config.base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/inquire-balance"
        response, payload = await self._request_json(
            "get",
            url,
            bucket=self._account_bucket(),
            params=params,
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis balance request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis balance request rejected: {msg}")

        next_tr_cont = str(response.headers.get("tr_cont") or "").upper().strip()
        return payload, next_tr_cont

    async def _fetch_us_balance_rows(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        all_rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}

        tr_cont = ""
        for _ in range(12):
            payload, next_tr_cont = await self._inquire_us_present_balance_page(
                tr_cont=tr_cont
            )

            output1 = payload.get("output1")
            if isinstance(output1, list):
                all_rows.extend(item for item in output1 if isinstance(item, dict))
            elif isinstance(output1, dict):
                all_rows.append(output1)

            output2 = payload.get("output2")
            if isinstance(output2, list) and output2:
                if isinstance(output2[0], dict):
                    summary = output2[0]
            elif isinstance(output2, dict):
                summary = output2

            if next_tr_cont not in {"M", "F"}:
                break
            tr_cont = "N"

        return all_rows, summary

    async def _inquire_us_present_balance_page(
        self, tr_cont: str = ""
    ) -> tuple[dict[str, Any], str]:
        token = await self._get_access_token()
        cano, product_code = self._account_parts()
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": product_code,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": self.config.tr_id_us_present_balance,
            "custtype": self.config.cust_type,
            "Accept": "application/json",
        }
        if tr_cont:
            headers["tr_cont"] = tr_cont

        url = f"{self.config.base_url.rstrip('/')}/uapi/overseas-stock/v1/trading/inquire-present-balance"
        response, payload = await self._request_json(
            "get",
            url,
            bucket=self._account_bucket(),
            params=params,
            headers=headers,
            timeout=httpx.Timeout(10.0),
        )
        if response.status_code >= 400:
            raise KISAPIError(f"kis us balance request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis us balance request rejected: {msg}")

        next_tr_cont = str(response.headers.get("tr_cont") or "").upper().strip()
        return payload, next_tr_cont

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        now = datetime.now(timezone.utc)
        if not force_refresh and self._access_token and now < self._token_expiry:
            return self._access_token

        async with self._token_lock:
            now = datetime.now(timezone.utc)
            if not force_refresh and self._access_token and now < self._token_expiry:
                return self._access_token
            if not force_refresh:
                cached = await self._rate_limiter.get_token(self._token_key)
                if cached is not None:
                    token, expiry = cached
                    # Refresh slightly early to avoid boundary expiry failures.
                    if token and now < expiry - timedelta(seconds=90):
                        self._access_token = token
                        self._token_expiry = expiry - timedelta(seconds=90)
                        return token
            else:
                self._clear_access_token()

            payload = {
                "grant_type": "client_credentials",
                "appkey": self.config.app_key,
                "appsecret": self.config.app_secret,
            }
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json",
            }
            url = f"{self.config.base_url.rstrip('/')}/oauth2/tokenP"
            response, body = await self._request_json(
                "post",
                url,
                bucket="token",
                retry_rate_limit=False,
                content=json.dumps(payload),
                headers=headers,
                timeout=httpx.Timeout(10.0),
            )
            if response.status_code >= 400:
                raise KISAPIError(f"kis token request failed: {body}")

            token = str(body.get("access_token") or "").strip()
            if not token:
                raise KISAPIError(f"kis token malformed response: {body}")

            expiry = self._resolve_token_expiry(body)
            # Refresh slightly early to avoid boundary expiry failures.
            self._access_token = token
            self._token_expiry = expiry - timedelta(seconds=90)
            await self._rate_limiter.save_token(
                token_key=self._token_key,
                access_token=token,
                expires_at=expiry,
            )
            return token

    def _clear_access_token(self) -> None:
        self._access_token = ""
        self._token_expiry = datetime.fromtimestamp(0, tz=timezone.utc)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        bucket: str = "rest",
        retry_rate_limit: bool = True,
        timeout: httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        method_name = str(method or "get").lower()
        timeout = timeout or httpx.Timeout(10.0)
        response: httpx.Response | None = None
        payload: dict[str, Any] = {}
        bucket_name = KISSharedRateLimiter._bucket_name(bucket)
        is_account_bucket = KISSharedRateLimiter._is_account_bucket(bucket_name)
        wait_buckets = self._rate_limit_buckets(bucket_name)
        attempts = 3 if retry_rate_limit and is_account_bucket else 2 if retry_rate_limit else 1
        for attempt in range(attempts):
            for wait_bucket in wait_buckets:
                await self._rate_limiter.wait(wait_bucket)
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method_name == "post":
                    response = await client.post(url, **kwargs)
                else:
                    response = await client.get(url, **kwargs)
            payload = self._parse_json(response)
            if (
                retry_rate_limit
                and attempt < attempts - 1
                and self._is_rest_rate_limit_payload(payload)
            ):
                retry_delay = self._rate_limit_retry_delay(bucket_name, attempt)
                for wait_bucket in wait_buckets:
                    await self._rate_limiter.penalize(wait_bucket, delay_sec=retry_delay)
                await asyncio.sleep(retry_delay)
                continue
            return response, payload
        if response is None:
            raise KISAPIError("kis request did not run")
        return response, payload

    @staticmethod
    def _rate_limit_buckets(bucket: str) -> list[str]:
        bucket_name = KISSharedRateLimiter._bucket_name(bucket)
        if (
            KISSharedRateLimiter._is_account_bucket(bucket_name)
            and bucket_name != "account"
        ):
            return ["account", bucket_name]
        return [bucket_name]

    def _rate_limit_retry_delay(self, bucket: str, attempt: int) -> float:
        rest_delay = 1.0 / max(float(self.config.rest_rate_limit_per_sec or 0.0), 1.0)
        if not KISSharedRateLimiter._is_account_bucket(bucket):
            return max(1.0, rest_delay * float(attempt + 1))
        account_interval = max(float(self.config.account_min_interval_sec or 0.0), 4.0)
        return max(10.0, account_interval * float(attempt + 2), rest_delay)

    @staticmethod
    def _is_rest_rate_limit_payload(payload: dict[str, Any]) -> bool:
        text = json.dumps(payload, ensure_ascii=False)
        return (
            "EGW00201" in text
            or "EGW00215" in text
            or "초당 거래건수" in text
            or "허용 가능한 초당" in text
            or "호출 유량" in text
        )

    @staticmethod
    def _is_access_token_expired_payload(payload: dict[str, Any]) -> bool:
        text = json.dumps(payload, ensure_ascii=False).lower()
        return (
            "egw00123" in text
            or "기간이 만료된 token" in text
            or "expired token" in text
            or "token expired" in text
        )

    def _to_assets(
        self, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []

        cash_breakdown = self._select_cash_breakdown(summary)
        cash_krw = cash_breakdown["cash_krw"]
        if cash_krw > 0:
            assets.append(
                {
                    "asset": "KRW",
                    "asset_name": "KRW",
                    "kind": "cash",
                    "qty": cash_krw,
                    "available": cash_breakdown["orderable_cash_krw"],
                    "locked": 0.0,
                    "avg_price": 1.0,
                    "mark_price": 1.0,
                    "value_krw": cash_krw,
                    "pnl_krw": 0.0,
                    **cash_breakdown,
                }
            )

        for row in rows:
            symbol = str(row.get("pdno") or row.get("mksc_shrn_iscd") or "").strip()
            if not symbol:
                continue
            symbol_name = (
                str(row.get("prdt_name") or row.get("prdt_abrv_name") or "").strip()
                or symbol
            )

            qty = self._to_float(row.get("hldg_qty"))
            if qty <= 0:
                continue

            available = self._to_float(row.get("ord_psbl_qty"))
            if available <= 0:
                available = qty
            locked = max(qty - available, 0.0)

            avg_price = self._to_float(row.get("pchs_avg_pric"))
            mark_price = self._to_float(row.get("prpr"))
            value_krw = self._to_float(row.get("evlu_amt"))
            if value_krw <= 0 and mark_price > 0:
                value_krw = qty * mark_price
            if value_krw <= 0:
                continue

            if mark_price <= 0 and qty > 0:
                mark_price = value_krw / qty
            pnl_krw = self._to_float(row.get("evlu_pfls_amt"))
            if pnl_krw == 0 and avg_price > 0 and mark_price > 0:
                pnl_krw = (mark_price - avg_price) * qty

            assets.append(
                {
                    "asset": symbol,
                    "asset_name": symbol_name,
                    "kind": "position",
                    "qty": qty,
                    "available": available,
                    "locked": locked,
                    "avg_price": avg_price,
                    "mark_price": mark_price,
                    "value_krw": value_krw,
                    "pnl_krw": pnl_krw,
                }
            )

        assets.sort(key=lambda item: (item["kind"] != "cash", str(item["asset"])))
        return assets

    def normalize_domestic_order_row(self, row: dict[str, Any]) -> dict[str, Any]:
        order_qty = _positive_int(row.get("ord_qty"))
        filled_qty = _positive_int(row.get("tot_ccld_qty"))
        remaining_qty = _positive_int(row.get("rmn_qty"))
        if remaining_qty <= 0 and order_qty > 0 and filled_qty < order_qty:
            cancel_confirmed = _positive_int(row.get("cnc_cfrm_qty"))
            rejected_qty = _positive_int(row.get("rjct_qty"))
            remaining_qty = max(order_qty - filled_qty - cancel_confirmed - rejected_qty, 0)
        total_amount = self._to_float(row.get("tot_ccld_amt") or row.get("prsm_tlex_smtl"))
        avg_price = self._to_float(row.get("avg_prvs") or row.get("pchs_avg_pric"))
        if avg_price <= 0 and filled_qty > 0 and total_amount > 0:
            avg_price = total_amount / filled_qty

        cancelable_qty = _positive_int(row.get("psbl_qty"))
        if cancelable_qty <= 0:
            cancelable_qty = remaining_qty

        return {
            "order_date": str(row.get("ord_dt") or ""),
            "order_no": self._first_text(row, "odno", "ODNO"),
            "order_orgno": self._first_text(
                row,
                "ord_gno_brno",
                "ORD_GNO_BRNO",
                "krx_fwdg_ord_orgno",
                "KRX_FWDG_ORD_ORGNO",
                "ord_orgno",
                "ORD_ORGNO",
            ),
            "original_order_no": self._first_text(row, "orgn_odno", "ORGN_ODNO"),
            "symbol": self._first_text(row, "pdno", "PDNO"),
            "name": self._first_text(row, "prdt_name", "PRDT_NAME"),
            "side_code": self._first_text(row, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD"),
            "side_name": self._first_text(
                row, "sll_buy_dvsn_cd_name", "SLL_BUY_DVSN_CD_NAME"
            ),
            "order_type": self._first_text(row, "ord_dvsn_cd", "ORD_DVSN_CD"),
            "order_type_name": self._first_text(row, "ord_dvsn_name", "ORD_DVSN_NAME"),
            "order_qty": order_qty,
            "order_price": int(self._to_float(row.get("ord_unpr"))),
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "cancelable_qty": cancelable_qty,
            "avg_fill_price": avg_price,
            "total_fill_amount": total_amount,
            "canceled": str(row.get("cncl_yn") or "").upper() == "Y",
            "order_time": str(row.get("ord_tmd") or ""),
            "exchange_id": self._first_text(
                row,
                "excg_id_dvsn_cd",
                "excg_id_dvsn_Cd",
                "EXCG_ID_DVSN_CD",
            ),
            "raw": row,
        }

    def _to_us_assets(
        self,
        rows: list[dict[str, Any]],
        summary: dict[str, Any],
        usd_krw_rate: float | None = None,
    ) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []

        resolved_usd_krw_rate = float(usd_krw_rate or 0.0)
        if resolved_usd_krw_rate <= 0:
            resolved_usd_krw_rate = self._select_usd_krw_rate(rows, summary)

        cash_usd, cash_krw = self._select_usd_cash_value(summary, resolved_usd_krw_rate)
        if cash_krw > 0:
            avg_price = resolved_usd_krw_rate if resolved_usd_krw_rate > 0 else 1.0
            assets.append(
                {
                    "asset": "USD",
                    "asset_name": "USD",
                    "kind": "cash",
                    "qty": cash_usd,
                    "available": cash_usd,
                    "locked": 0.0,
                    "avg_price": avg_price,
                    "mark_price": avg_price,
                    "value_krw": cash_krw,
                    "pnl_krw": 0.0,
                }
            )

        for row in rows:
            symbol = str(
                row.get("pdno") or row.get("ovrs_pdno") or row.get("std_pdno") or ""
            ).strip()
            if not symbol:
                continue
            symbol_name = (
                str(
                    row.get("ovrs_item_name")
                    or row.get("prdt_name")
                    or row.get("prdt_abrv_name")
                    or row.get("tr_mket_name")
                    or symbol
                ).strip()
                or symbol
            )

            qty = self._to_float(row.get("cblc_qty13"))
            if qty <= 0:
                qty = self._to_float(row.get("ovrs_cblc_qty"))
            if qty <= 0:
                qty = self._to_float(row.get("hldg_qty"))
            if qty <= 0:
                continue

            available = self._to_float(row.get("ord_psbl_qty1"))
            if available <= 0:
                available = self._to_float(row.get("ord_psbl_qty"))
            if available <= 0:
                available = qty
            locked = max(qty - available, 0.0)

            avg_usd = self._to_float(row.get("avg_unpr3"))
            if avg_usd <= 0:
                avg_usd = self._to_float(row.get("pchs_avg_pric"))
            mark_usd = self._to_float(row.get("ovrs_now_pric1"))
            if mark_usd <= 0:
                mark_usd = self._to_float(row.get("now_pric2"))

            row_fx = float(usd_krw_rate or 0.0)
            if row_fx <= 0:
                row_fx = self._to_float(row.get("bass_exrt"))
            if row_fx <= 0:
                row_fx = resolved_usd_krw_rate

            value_foreign = self._to_float(row.get("frcr_evlu_amt2"))
            if value_foreign <= 0:
                value_foreign = self._to_float(row.get("ovrs_stck_evlu_amt"))
            if value_foreign <= 0 and mark_usd > 0:
                value_foreign = qty * mark_usd

            value_krw = value_foreign * row_fx if row_fx > 0 else 0.0
            if value_krw <= 0:
                value_krw = self._to_float(row.get("pchs_rmnd_wcrc_amt"))
            if value_krw <= 0:
                continue

            pnl_foreign = self._to_float(row.get("evlu_pfls_amt2"))
            if pnl_foreign == 0:
                pnl_foreign = self._to_float(row.get("frcr_evlu_pfls_amt"))
            pnl_krw = pnl_foreign * row_fx if row_fx > 0 else 0.0
            if pnl_krw == 0 and avg_usd > 0 and mark_usd > 0 and row_fx > 0:
                pnl_krw = (mark_usd - avg_usd) * qty * row_fx

            if mark_usd <= 0:
                mark_usd = value_foreign / qty if qty > 0 else 0.0
            avg_price = avg_usd * row_fx if row_fx > 0 else avg_usd
            mark_price = mark_usd * row_fx if row_fx > 0 else mark_usd

            assets.append(
                {
                    "asset": symbol,
                    "asset_name": symbol_name,
                    "kind": "position",
                    "qty": qty,
                    "available": available,
                    "locked": locked,
                    "avg_price": avg_price,
                    "mark_price": mark_price,
                    "value_krw": value_krw,
                    "pnl_krw": pnl_krw,
                }
            )

        assets.sort(key=lambda item: (item["kind"] != "cash", str(item["asset"])))
        return assets

    def _select_cash_value(self, summary: dict[str, Any]) -> float:
        return self._select_cash_breakdown(summary)["cash_krw"]

    def _select_cash_breakdown(self, summary: dict[str, Any]) -> dict[str, float]:
        if not isinstance(summary, dict):
            return {
                "cash_krw": 0.0,
                "settled_cash_krw": 0.0,
                "orderable_cash_krw": 0.0,
                "receivable_cash_krw": 0.0,
                "settlement_cash_krw": 0.0,
                "next_day_cash_krw": 0.0,
                "net_asset_krw": 0.0,
                "today_sell_amount_krw": 0.0,
                "today_fee_tax_krw": 0.0,
            }

        settled_cash = self._to_float(summary.get("dnca_tot_amt"))
        orderable_cash = self._to_float(summary.get("ord_psbl_cash"))
        settlement_cash = self._to_float(summary.get("prvs_rcdl_excc_amt"))
        next_day_cash = self._to_float(summary.get("nxdy_excc_amt"))
        net_asset = self._to_float(summary.get("nass_amt") or summary.get("tot_evlu_amt"))
        stock_value = self._to_float(summary.get("scts_evlu_amt"))
        inferred_cash = max(net_asset - stock_value, 0.0) if net_asset > 0 else 0.0
        today_sell_amount = self._to_float(summary.get("thdt_sll_amt"))
        today_fee_tax = self._to_float(summary.get("thdt_tlex_amt"))

        cash_krw = max(
            settled_cash,
            settlement_cash,
            orderable_cash,
            next_day_cash,
            inferred_cash,
        )
        if orderable_cash <= 0:
            # Some KIS balance payloads omit explicit orderable cash but include
            # provisional settlement cash after same-day sells. Keep the broker
            # order endpoint as the final gate, but expose this buying power to
            # the block manager so the cash does not appear to vanish.
            orderable_cash = max(settlement_cash, settled_cash, next_day_cash)

        return {
            "cash_krw": cash_krw,
            "settled_cash_krw": settled_cash if settled_cash > 0 else cash_krw,
            "orderable_cash_krw": orderable_cash,
            "receivable_cash_krw": max(settlement_cash - settled_cash, 0.0),
            "settlement_cash_krw": settlement_cash,
            "next_day_cash_krw": next_day_cash,
            "net_asset_krw": net_asset,
            "today_sell_amount_krw": today_sell_amount,
            "today_fee_tax_krw": today_fee_tax,
        }


    def _select_usd_krw_rate(
        self, rows: list[dict[str, Any]], summary: dict[str, Any]
    ) -> float:
        for row in rows:
            rate = self._to_float(row.get("bass_exrt"))
            if rate > 0:
                return rate
        for key in ("frst_bltn_exrt", "bass_exrt"):
            rate = self._to_float(summary.get(key))
            if rate > 0:
                return rate
        return 0.0

    def _select_usd_cash_value(
        self, summary: dict[str, Any], usd_krw_rate: float
    ) -> tuple[float, float]:
        if not isinstance(summary, dict):
            return 0.0, 0.0

        cash_usd = 0.0
        for key in (
            "frcr_dncl_amt_2",
            "frcr_use_psbl_amt",
            "tot_frcr_cblc_smtl",
        ):
            value = self._to_float(summary.get(key))
            if value > 0:
                cash_usd = value
                break

        cash_krw = cash_usd * usd_krw_rate if cash_usd > 0 and usd_krw_rate > 0 else 0.0
        if cash_krw > 0:
            return cash_usd, cash_krw

        for key in ("tot_dncl_amt", "dncl_amt", "wdrw_psbl_tot_amt"):
            value_krw = self._to_float(summary.get(key))
            if value_krw <= 0:
                continue
            inferred_usd = value_krw / usd_krw_rate if usd_krw_rate > 0 else value_krw
            return inferred_usd, value_krw

        return 0.0, 0.0

    def _account_parts(self) -> tuple[str, str]:
        cano = str(self.config.account_no or "").strip()
        prod = str(self.config.product_code or "").strip()

        if "-" in cano and not prod:
            left, right = cano.split("-", 1)
            return left.strip(), right.strip()

        if len(cano) > 8 and not prod:
            return cano[:8], cano[8:10]

        return cano, prod

    def _resolve_token_expiry(self, body: dict[str, Any]) -> datetime:
        now = datetime.now(timezone.utc)
        expires_in = self._to_float(body.get("expires_in"))
        if expires_in > 0:
            return now + timedelta(seconds=expires_in)

        text = str(body.get("access_token_token_expired") or "").strip()
        if text:
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    parsed = datetime.strptime(text, fmt)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
                    return parsed.astimezone(timezone.utc)
                except ValueError:
                    continue

        return now + timedelta(hours=23)

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
    def _first_text(row: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = row.get(key)
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _normalize_yyyymmdd(value: str | None) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        digits = "".join(ch for ch in text if ch.isdigit())
        return digits[:8] if len(digits) >= 8 else ""

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise KISAPIError(f"non-json response from kis: {exc}") from exc
        if not isinstance(payload, dict):
            raise KISAPIError("kis response malformed")
        return payload
