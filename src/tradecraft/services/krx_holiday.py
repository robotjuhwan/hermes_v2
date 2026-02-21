from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

_KRX_HOLIDAY_PAGE_URL = (
    "https://global.krx.co.kr/contents/GLB/05/0501/0501110000/GLB0501110000.jsp"
)
_KRX_OTP_URL = "https://global.krx.co.kr/contents/COM/GenerateOTP.jspx"
_KRX_DATA_URL = "https://global.krx.co.kr/contents/GLB/99/GLB99000001.jspx"
_KRX_BLD = "GLB/05/0501/0501110000/glb0501110000_01"
_DATE_DIGITS = re.compile(r"\D")


@dataclass(slots=True)
class KRXHolidayCalendarConfig:
    timeout_sec: float = 10.0
    cache_ttl_hours: int = 12


class KRXHolidayCalendar:
    def __init__(self, config: KRXHolidayCalendarConfig | None = None) -> None:
        self.config = config or KRXHolidayCalendarConfig()
        self._cache: dict[int, set[date]] = {}
        self._cache_updated_at: dict[int, datetime] = {}

    def is_open_day(self, value: date) -> bool:
        if value.weekday() >= 5:
            return False

        holidays = self._get_year_holidays(value.year)
        if holidays is None:
            return True
        return value not in holidays

    def _get_year_holidays(self, year: int) -> set[date] | None:
        now = datetime.utcnow()
        updated = self._cache_updated_at.get(year)
        if updated is not None:
            ttl = timedelta(hours=max(int(self.config.cache_ttl_hours), 1))
            if now - updated <= ttl:
                return self._cache.get(year, set())

        holidays = self._fetch_year_holidays(year)
        if holidays is None:
            return self._cache.get(year)

        self._cache[year] = holidays
        self._cache_updated_at[year] = now
        return holidays

    def _fetch_year_holidays(self, year: int) -> set[date] | None:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": _KRX_HOLIDAY_PAGE_URL,
        }
        try:
            timeout = httpx.Timeout(max(float(self.config.timeout_sec), 1.0))
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                client.get(_KRX_HOLIDAY_PAGE_URL, headers=headers)
                otp_response = client.get(
                    _KRX_OTP_URL,
                    params={"name": "form", "bld": _KRX_BLD},
                    headers=headers,
                )
                otp_response.raise_for_status()
                code = otp_response.text.strip()
                if not code:
                    logger.warning("krx holiday otp missing")
                    return None

                data_response = client.post(
                    _KRX_DATA_URL,
                    data={
                        "search_bas_yy": str(int(year)),
                        "gridTp": "KRX",
                        "code": code,
                    },
                    headers=headers,
                )
                data_response.raise_for_status()
        except Exception as exc:
            logger.warning("krx holiday fetch failed: %s", exc)
            return None

        text = data_response.text.strip()
        if not text:
            return set()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("krx holiday payload not json")
            return None
        if not isinstance(payload, dict):
            return None

        first_key = next(iter(payload.keys()), "")
        rows = payload.get(first_key)
        if not isinstance(rows, list):
            return set()

        out: set[date] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = str(row.get("calnd_dd") or row.get("calnd_dd_dy") or "").strip()
            parsed = _parse_krx_date(raw)
            if parsed is None or parsed.year != year:
                continue
            out.add(parsed)
        return out


def _parse_krx_date(raw: str) -> date | None:
    text = _DATE_DIGITS.sub("", str(raw or "").strip())
    if len(text) != 8:
        return None
    try:
        year = int(text[0:4])
        month = int(text[4:6])
        day = int(text[6:8])
        return date(year=year, month=month, day=day)
    except ValueError:
        return None
