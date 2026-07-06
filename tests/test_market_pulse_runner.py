from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tradecraft.runtime.market_pulse_runner import (
    _load_kis_block_context,
    _market_pulse_sleep_interval,
)


def test_market_pulse_runner_loads_active_kis_block_context(tmp_path: Path) -> None:
    db_path = tmp_path / "kis_blocks.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                qty_initial INTEGER NOT NULL,
                qty_open INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                price REAL,
                source TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, name, qty_initial, qty_open, status, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk_005930",
                "005930",
                "삼성전자",
                3,
                3,
                "open",
                "{}",
                "2026-06-29T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO blocks (
                block_id, symbol, name, qty_initial, qty_open, status, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "blk_closed",
                "000660",
                "SK하이닉스",
                1,
                0,
                "closed",
                "{}",
                "2026-06-29T00:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO quote_snapshots (
                symbol, name, price, source, fetched_at, status, error_message, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "005930",
                "삼성전자",
                79000,
                "kis",
                "2026-06-29T00:01:00+00:00",
                "ok",
                "",
                json.dumps(
                    {
                        "bstp_kor_isnm": "반도체",
                        "rprs_mrkt_kor_name": "KOSPI",
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    context = _load_kis_block_context(str(db_path))

    assert context["status"] == "ok"
    assert context["blocks"] == [
        {
            "block_id": "blk_005930",
            "symbol": "005930",
            "name": "삼성전자",
            "status": "open",
            "qty_initial": 3,
            "qty_open": 3,
            "sector": "",
            "market": "",
        }
    ]
    assert context["quotes"]["005930"]["raw"] == {
        "bstp_kor_isnm": "반도체",
        "rprs_mrkt_kor_name": "KOSPI",
    }


def test_market_pulse_sleep_interval_slows_down_when_krx_closed() -> None:
    class Settings:
        market_pulse_interval_sec = 60
        market_pulse_closed_interval_sec = 1800

    assert _market_pulse_sleep_interval(
        Settings(),
        {"session": "regular", "is_market_open": True},
    ) == 60
    assert _market_pulse_sleep_interval(
        Settings(),
        {"session": "closed", "is_market_open": False},
    ) == 1800
    assert _market_pulse_sleep_interval(
        Settings(),
        {"session": "post_close_review", "is_market_open": False},
    ) == 1800
