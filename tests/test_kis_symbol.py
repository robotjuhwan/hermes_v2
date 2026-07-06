from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_symbol import clean_symbol_name, extract_symbol_name


ROOT = Path(__file__).resolve().parents[1]


def test_kis_symbol_name_helpers_live_outside_block_trader() -> None:
    trader_source = (ROOT / "src/tradecraft/services/kis_block_trader.py").read_text()
    symbol_source = (ROOT / "src/tradecraft/services/kis_symbol.py").read_text()

    assert "def clean_symbol_name(" in symbol_source
    assert "def extract_symbol_name(" in symbol_source
    assert "def _clean_symbol_name(" not in trader_source
    assert "def _extract_symbol_name(" not in trader_source


def test_clean_symbol_name_rejects_codes_generic_text_and_html() -> None:
    assert clean_symbol_name("삼성전자", symbol="005930") == "삼성전자"
    assert clean_symbol_name("005930", symbol="005930") == ""
    assert clean_symbol_name("178920", symbol="178920") == ""
    assert clean_symbol_name("정보", symbol="178920") == ""
    assert clean_symbol_name("<b>삼성전자</b>", symbol="005930") == ""


def test_extract_symbol_name_prefers_kis_fields_and_nested_payloads() -> None:
    payload = {
        "prdt_name": "",
        "raw": {
            "output": [
                {"name": "정보"},
                {"hts_kor_isnm": "SK하이닉스"},
            ]
        },
    }

    assert extract_symbol_name(payload, symbol="000660") == "SK하이닉스"
