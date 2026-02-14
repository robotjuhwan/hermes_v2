from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx


class KISAPIError(RuntimeError):
    pass


@dataclass
class KISConfig:
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    product_code: str = ""
    base_url: str = "https://openapi.koreainvestment.com:9443"
    tr_id_balance: str = "TTTC8434R"
    tr_id_us_present_balance: str = "CTRP6504R"
    cust_type: str = "P"

    @property
    def ready(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_no and self.product_code)


class KISAdapter:
    def __init__(self, config: KISConfig) -> None:
        self.config = config
        self._access_token = ""
        self._token_expiry = datetime.fromtimestamp(0, tz=timezone.utc)
        self._token_lock = asyncio.Lock()

    async def fetch_balance_assets(self) -> list[dict[str, Any]]:
        rows, summary = await self._fetch_balance_rows()
        return self._to_assets(rows, summary)

    async def fetch_us_balance_assets(self) -> list[dict[str, Any]]:
        rows, summary = await self._fetch_us_balance_rows()
        return self._to_us_assets(rows, summary)

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
        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=headers)

        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise KISAPIError(f"kis balance request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis balance request rejected: {msg}")

        next_tr_cont = str(response.headers.get("tr_cont") or "").upper().strip()
        return payload, next_tr_cont

    async def _fetch_us_balance_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not self.config.ready:
            raise KISAPIError("kis config missing")

        all_rows: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}

        tr_cont = ""
        for _ in range(12):
            payload, next_tr_cont = await self._inquire_us_present_balance_page(tr_cont=tr_cont)

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

    async def _inquire_us_present_balance_page(self, tr_cont: str = "") -> tuple[dict[str, Any], str]:
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
        timeout = httpx.Timeout(10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=headers)

        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise KISAPIError(f"kis us balance request failed: {payload}")
        if str(payload.get("rt_cd")) != "0":
            msg = str(payload.get("msg1") or payload.get("msg_cd") or payload)
            raise KISAPIError(f"kis us balance request rejected: {msg}")

        next_tr_cont = str(response.headers.get("tr_cont") or "").upper().strip()
        return payload, next_tr_cont

    async def _get_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._access_token and now < self._token_expiry:
            return self._access_token

        async with self._token_lock:
            now = datetime.now(timezone.utc)
            if self._access_token and now < self._token_expiry:
                return self._access_token

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
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, content=json.dumps(payload), headers=headers)

            body = self._parse_json(response)
            if response.status_code >= 400:
                raise KISAPIError(f"kis token request failed: {body}")

            token = str(body.get("access_token") or "").strip()
            if not token:
                raise KISAPIError(f"kis token malformed response: {body}")

            expiry = self._resolve_token_expiry(body)
            # Refresh slightly early to avoid boundary expiry failures.
            self._access_token = token
            self._token_expiry = expiry - timedelta(seconds=90)
            return token

    def _to_assets(self, rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []

        cash_krw = self._select_cash_value(summary)
        if cash_krw > 0:
            assets.append(
                {
                    "asset": "KRW",
                    "asset_name": "KRW",
                    "kind": "cash",
                    "qty": cash_krw,
                    "available": cash_krw,
                    "locked": 0.0,
                    "avg_price": 1.0,
                    "mark_price": 1.0,
                    "value_krw": cash_krw,
                    "pnl_krw": 0.0,
                }
            )

        for row in rows:
            symbol = str(row.get("pdno") or row.get("mksc_shrn_iscd") or "").strip()
            if not symbol:
                continue
            symbol_name = str(row.get("prdt_name") or row.get("prdt_abrv_name") or "").strip() or symbol

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

    def _to_us_assets(self, rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []

        usd_krw_rate = self._select_usd_krw_rate(rows, summary)
        cash_usd, cash_krw = self._select_usd_cash_value(summary, usd_krw_rate)
        if cash_krw > 0:
            avg_price = usd_krw_rate if usd_krw_rate > 0 else 1.0
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
            symbol = str(row.get("pdno") or row.get("ovrs_pdno") or row.get("std_pdno") or "").strip()
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

            row_fx = self._to_float(row.get("bass_exrt"))
            if row_fx <= 0:
                row_fx = usd_krw_rate

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
        if not isinstance(summary, dict):
            return 0.0

        for key in (
            "dnca_tot_amt",
            "ord_psbl_cash",
            "nass_amt",
            "tot_evlu_amt",
        ):
            value = self._to_float(summary.get(key))
            if value > 0:
                if key == "tot_evlu_amt":
                    scts = self._to_float(summary.get("scts_evlu_amt"))
                    inferred_cash = max(value - scts, 0.0)
                    if inferred_cash > 0:
                        return inferred_cash
                else:
                    return value
        return 0.0

    def _select_usd_krw_rate(self, rows: list[dict[str, Any]], summary: dict[str, Any]) -> float:
        for row in rows:
            rate = self._to_float(row.get("bass_exrt"))
            if rate > 0:
                return rate
        for key in ("frst_bltn_exrt", "bass_exrt"):
            rate = self._to_float(summary.get(key))
            if rate > 0:
                return rate
        return 0.0

    def _select_usd_cash_value(self, summary: dict[str, Any], usd_krw_rate: float) -> tuple[float, float]:
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
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
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
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise KISAPIError(f"non-json response from kis: {exc}") from exc
        if not isinstance(payload, dict):
            raise KISAPIError("kis response malformed")
        return payload
