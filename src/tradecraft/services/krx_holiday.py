from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_KRX_HOLIDAY_PAGE_URL = (
    "https://global.krx.co.kr/contents/GLB/05/0501/0501110000/GLB0501110000.jsp"
)
_KRX_OTP_URL = "https://global.krx.co.kr/contents/COM/GenerateOTP.jspx"
_KRX_DATA_URL = "https://global.krx.co.kr/contents/GLB/99/GLB99000001.jspx"
_KRX_BLD = "GLB/05/0501/0501110000/glb0501110000_01"
_DATE_DIGITS = re.compile(r"\D")
_GLOBAL_CACHE: dict[int, set[date]] = {}
_GLOBAL_CACHE_UPDATED_AT: dict[int, datetime] = {}
_GLOBAL_FAILURE_UPDATED_AT: dict[int, datetime] = {}


@dataclass(slots=True)
class KRXHolidayCalendarConfig:
    timeout_sec: float = 2.0
    cache_ttl_hours: int = 12
    failure_cache_ttl_sec: int = 1800
    cache_path: str = ".runtime/krx_holidays.json"


class KRXHolidayCalendar:
    def __init__(self, config: KRXHolidayCalendarConfig | None = None) -> None:
        self.config = config or KRXHolidayCalendarConfig()
        self._cache = _GLOBAL_CACHE
        self._cache_updated_at = _GLOBAL_CACHE_UPDATED_AT
        self._failure_updated_at = _GLOBAL_FAILURE_UPDATED_AT
        self._disk_loaded = False

    def is_open_day(self, value: date) -> bool:
        if value.weekday() >= 5:
            return False

        holidays = self._get_year_holidays(value.year)
        if holidays is None:
            return True
        return value not in holidays

    def _get_year_holidays(self, year: int) -> set[date] | None:
        now = datetime.utcnow()
        self._load_disk_cache_once()
        updated = self._cache_updated_at.get(year)
        if updated is not None:
            ttl = timedelta(hours=max(int(self.config.cache_ttl_hours), 1))
            if now - updated <= ttl:
                return self._cache.get(year, set())

        failure_updated = self._failure_updated_at.get(year)
        if failure_updated is not None:
            ttl = timedelta(seconds=max(int(self.config.failure_cache_ttl_sec), 60))
            if now - failure_updated <= ttl:
                return self._cache.get(year)

        holidays = self._fetch_year_holidays(year)
        if holidays is None:
            self._failure_updated_at[year] = now
            self._write_disk_cache()
            return self._cache.get(year)

        self._cache[year] = holidays
        self._cache_updated_at[year] = now
        self._failure_updated_at.pop(year, None)
        self._write_disk_cache()
        return holidays

    def _cache_path(self) -> Path | None:
        raw = str(self.config.cache_path or "").strip()
        if not raw:
            return None
        return Path(raw)

    def _load_disk_cache_once(self) -> None:
        if self._disk_loaded:
            return
        self._disk_loaded = True
        path = self._cache_path()
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("krx holiday disk cache read failed: %s", exc)
            return
        if not isinstance(payload, dict):
            return
        holidays_by_year = payload.get("holidays")
        if isinstance(holidays_by_year, dict):
            for raw_year, raw_dates in holidays_by_year.items():
                try:
                    year = int(raw_year)
                except (TypeError, ValueError):
                    continue
                if not isinstance(raw_dates, list):
                    continue
                parsed_dates = {
                    parsed
                    for parsed in (_parse_krx_date(str(value)) for value in raw_dates)
                    if parsed is not None and parsed.year == year
                }
                self._cache[year] = parsed_dates
        updated_at = payload.get("updated_at")
        if isinstance(updated_at, dict):
            for raw_year, raw_value in updated_at.items():
                parsed = _parse_datetime(raw_value)
                if parsed is None:
                    continue
                try:
                    self._cache_updated_at[int(raw_year)] = parsed
                except (TypeError, ValueError):
                    continue
        failures = payload.get("failure_updated_at")
        if isinstance(failures, dict):
            for raw_year, raw_value in failures.items():
                parsed = _parse_datetime(raw_value)
                if parsed is None:
                    continue
                try:
                    self._failure_updated_at[int(raw_year)] = parsed
                except (TypeError, ValueError):
                    continue

    def _write_disk_cache(self) -> None:
        path = self._cache_path()
        if path is None:
            return
        try:
            if path.parent != Path("."):
                path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "holidays": {
                    str(year): [
                        value.isoformat()
                        for value in sorted(values)
                    ]
                    for year, values in sorted(self._cache.items())
                },
                "updated_at": {
                    str(year): value.isoformat()
                    for year, value in sorted(self._cache_updated_at.items())
                },
                "failure_updated_at": {
                    str(year): value.isoformat()
                    for year, value in sorted(self._failure_updated_at.items())
                },
            }
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(path)
        except Exception as exc:
            logger.debug("krx holiday disk cache write failed: %s", exc)

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


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed
