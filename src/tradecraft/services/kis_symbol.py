from __future__ import annotations

import re
from typing import Any


def _clean_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def clean_symbol_name(value: Any, *, symbol: str = "") -> str:
    text = _clean_text(value, limit=80)
    code = str(symbol or "").strip()
    if not text or text == code or _is_symbol(text):
        return ""
    if text in {"정보", "투자", "종목", "종목명", "코드"}:
        return ""
    if "<" in text or ">" in text:
        return ""
    return text


def extract_symbol_name(value: Any, *, symbol: str) -> str:
    if isinstance(value, dict):
        for key in (
            "prdt_name",
            "hts_kor_isnm",
            "asset_name",
            "name",
            "company_name",
            "prdt_abrv_name",
        ):
            name = clean_symbol_name(value.get(key), symbol=symbol)
            if name:
                return name
        for key in ("raw", "response", "output", "data"):
            name = extract_symbol_name(value.get(key), symbol=symbol)
            if name:
                return name
    if isinstance(value, list):
        for item in value[:8]:
            name = extract_symbol_name(item, symbol=symbol)
            if name:
                return name
    return ""
