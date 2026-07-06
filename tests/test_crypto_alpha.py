from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services.crypto_alpha import (
    CryptoAlphaConfig,
    CryptoAlphaService,
    extract_symbol_links,
    html_to_text,
)


def _age_events(service: CryptoAlphaService, *, hours: int = 48) -> None:
    old_at = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with service.repository._connect() as conn:
        conn.execute(
            "UPDATE crypto_alpha_events SET detected_at=?, event_time=?",
            (old_at, old_at),
        )


def test_crypto_alpha_schema_initializes(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )

    status = service.status()

    assert status["status"] == "ok"
    assert status["db_path"].endswith("crypto_alpha.db")
    assert status["sources"] >= 3
    assert status["snapshots"] == 0
    assert status["events"] == 0
    assert status["outcomes"] == 0


def test_html_to_text_extracts_public_announcement_text() -> None:
    html = """
    <html><head><script>ignored()</script></head>
    <body><h1>Binance Will List ACME (ACME)</h1>
    <p>Trading will open for ACME/USDT at 2026-05-23 10:00 UTC.</p></body></html>
    """

    text = html_to_text(html)

    assert "Binance Will List ACME (ACME)" in text
    assert "Trading will open for ACME/USDT" in text
    assert "ignored" not in text


def test_extract_symbol_links_ignores_generic_parenthetical_words() -> None:
    text = """
    Email (Required)
    Coinbase Australia Receives AFSL Licence: Australian Financial Services Licence (AFSL)
    Coinbase Receives Conditional OCC Approval from the Office of the
    Comptroller of the Currency (OCC)
    """

    links = extract_symbol_links(text)

    assert links == []


def test_extract_symbol_links_keeps_listing_context_parenthetical_symbol() -> None:
    links = extract_symbol_links("Binance Will List ACME (ACME)")

    assert links == [
        {
            "symbol": "ACMEUSDT",
            "base_asset": "ACME",
            "link_confidence": 0.86,
            "impact_direction": "bullish_watch",
            "impact_horizon": "1h_72h",
            "reason": "symbol appeared in public catalyst text",
        }
    ]


def test_snapshot_insert_dedupes_by_source_and_hash(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )

    first = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME)",
        raw_json={"fixture": True},
    )
    second = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME)",
        raw_json={"fixture": True},
    )

    assert first["snapshot_id"] == second["snapshot_id"]
    assert service.status()["snapshots"] == 1


def test_extracts_listing_event_and_links_symbol(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text=(
            "Binance Will List ACME (ACME). "
            "Trading will open for ACME/USDT spot trading pair."
        ),
        raw_json={"fixture": True},
    )

    result = service.extract_events_from_snapshot(snapshot["snapshot_id"])
    context = service.context_pack(symbols=["ACMEUSDT"], limit=5)

    assert result["created_events"] == 1
    assert context["events"][0]["event_type"] == "listing"
    assert context["events"][0]["symbols"] == ["ACMEUSDT"]
    assert context["events"][0]["source_id"] == "binance_announcements"


def test_crypto_alpha_context_includes_normalized_evidence(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/test",
        title="Binance Will List TEST (TEST)",
        raw_text="Binance Will List TEST (TEST). Trading will open for TEST/USDT.",
    )
    service.extract_events_from_snapshot(snapshot["snapshot_id"])

    context = service.context_pack(symbols=["TESTUSDT"], limit=5)

    assert context["evidence"][0]["source"] == "crypto_alpha"
    assert context["evidence"][0]["signal_type"] == "catalyst_event"
    assert context["evidence"][0]["symbol"] == "TESTUSDT"


def test_extract_events_from_snapshot_is_idempotent(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME). Trading will open for ACME/USDT.",
    )

    first = service.extract_events_from_snapshot(snapshot["snapshot_id"])
    second = service.extract_events_from_snapshot(snapshot["snapshot_id"])

    assert first["created_events"] == 1
    assert second["created_events"] == 0
    assert first["event_ids"] == second["event_ids"]
    assert service.status()["events"] == 1


def test_labels_event_outcome_from_binance_klines(tmp_path: Path) -> None:
    class FakeBinance:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1h",
            limit: int = 80,
            start_time: int | None = None,
            end_time: int | None = None,
        ) -> list[dict[str, Any]]:
            assert symbol == "ACMEUSDT"
            assert market == "spot"
            assert interval == "1h"
            assert limit in {3, 6, 26, 74}
            assert start_time is not None
            assert end_time is not None
            self.calls.append(
                {"limit": limit, "start_time": start_time, "end_time": end_time}
            )
            return [
                {"open": 100, "high": 101, "low": 99, "close": 100, "open_time": 1},
                {"open": 100, "high": 112, "low": 97, "close": 108, "open_time": 2},
                {"open": 108, "high": 116, "low": 104, "close": 112, "open_time": 3},
            ]

    fake_binance = FakeBinance()
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db")),
        binance=fake_binance,
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME). Trading will open for ACME/USDT.",
    )
    service.extract_events_from_snapshot(snapshot["snapshot_id"])
    _age_events(service, hours=48)

    result = asyncio.run(service.label_due_outcomes())
    context = service.context_pack(symbols=["ACMEUSDT"], limit=5)

    assert result["status"] == "ok"
    assert result["labeled"] == 3
    assert result["skipped_recent"] == 1
    assert fake_binance.calls
    assert {call["limit"] for call in fake_binance.calls} == {3, 6, 26}
    assert context["similar_outcomes"][0]["symbol"] == "ACMEUSDT"
    assert context["similar_outcomes"][0]["horizon"] == "24h"
    assert context["similar_outcomes"][0]["return_pct"] == pytest.approx(12.0)
    assert context["similar_outcomes"][0]["mfe_pct"] == pytest.approx(16.0)
    assert context["similar_outcomes"][0]["mae_pct"] == pytest.approx(-3.0)


def test_outcome_labeling_skips_invalid_symbol_without_stopping_cycle(
    tmp_path: Path,
) -> None:
    class FakeBinance:
        def __init__(self) -> None:
            self.calls_by_symbol: dict[str, int] = {}

        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1h",
            limit: int = 80,
            start_time: int | None = None,
            end_time: int | None = None,
        ) -> list[dict[str, Any]]:
            self.calls_by_symbol[symbol] = self.calls_by_symbol.get(symbol, 0) + 1
            if symbol == "BADUSDT":
                raise RuntimeError("binance spot public request failed: {'code': -1121}")
            return [
                {"open": 100, "high": 101, "low": 99, "close": 100, "open_time": 1},
                {"open": 100, "high": 112, "low": 97, "close": 108, "open_time": 2},
            ]

    fake_binance = FakeBinance()
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db")),
        binance=fake_binance,
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme-bad",
        title="Binance Will List ACME (ACME) and BAD (BAD)",
        raw_text="Trading will open for ACME/USDT and BAD/USDT spot trading pairs.",
    )
    service.extract_events_from_snapshot(snapshot["snapshot_id"])
    _age_events(service, hours=48)

    result = asyncio.run(service.label_due_outcomes())
    second = asyncio.run(service.label_due_outcomes())

    assert result["status"] == "ok"
    assert result["labeled"] == 3
    assert any(error["symbol"] == "BADUSDT" for error in result["errors"])
    assert second["errors"] == []
    assert fake_binance.calls_by_symbol["BADUSDT"] == 1
    with service.repository._connect() as conn:
        row = conn.execute(
            """
            SELECT validity_status, validity_reason
            FROM crypto_alpha_event_symbols
            WHERE symbol='BADUSDT'
            """
        ).fetchone()
    assert row["validity_status"] == "invalid"
    assert row["validity_reason"] == "binance_spot_invalid_symbol"


def test_context_pack_excludes_invalid_linked_symbols_but_keeps_valid(
    tmp_path: Path,
) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db"))
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/mixed",
        title=(
            "Binance Will List ACME (ACME), REQUIRED (REQUIRED), "
            "OCC (OCC), and AFSL (AFSL)"
        ),
        raw_text=(
            "Trading will open for ACME/USDT, REQUIRED/USDT, "
            "OCC/USDT, and AFSL/USDT spot trading pairs."
        ),
    )
    extracted = service.extract_events_from_snapshot(snapshot["snapshot_id"])
    event_id = extracted["event_ids"][0]
    for symbol in ["REQUIREDUSDT", "OCCUSDT", "AFSLUSDT"]:
        service.repository.mark_event_symbol_validity(
            event_id=event_id,
            symbol=symbol,
            status="invalid",
            reason="binance_spot_invalid_symbol",
        )
        service.repository.upsert_outcome(
            event_id=event_id,
            symbol=symbol,
            horizon="1h",
            return_pct=99.0,
            mfe_pct=99.0,
            mae_pct=0.0,
            r_multiple=99.0,
        )
    service.repository.upsert_outcome(
        event_id=event_id,
        symbol="ACMEUSDT",
        horizon="1h",
        return_pct=7.0,
        mfe_pct=9.0,
        mae_pct=-2.0,
        r_multiple=3.5,
    )

    service.refresh_scorecards()
    context = service.context_pack(
        symbols=["ACMEUSDT", "REQUIREDUSDT", "OCCUSDT", "AFSLUSDT"],
        limit=5,
    )

    assert context["event_count"] == 1
    assert context["events"][0]["symbols"] == ["ACMEUSDT"]
    assert {item["symbol"] for item in context["evidence"]} == {"ACMEUSDT"}
    assert {item["symbol"] for item in context["similar_outcomes"]} == {"ACMEUSDT"}
    assert context["scorecards"][0]["support_count"] == 1
    assert context["scorecards"][0]["avg_r_multiple"] == pytest.approx(3.5)
    assert context["scorecards"][0]["win_rate_pct"] == pytest.approx(100.0)
    assert context["scorecards"][0]["status"] == "candidate"
    assert context["active_lessons"] == []
    with service.repository._connect() as conn:
        invalid_rows = conn.execute(
            """
            SELECT COUNT(*)
            FROM crypto_alpha_event_symbols
            WHERE symbol IN ('REQUIREDUSDT', 'OCCUSDT', 'AFSLUSDT')
            """
        ).fetchone()[0]
    assert invalid_rows == 3


def test_outcome_labeling_reports_and_skips_previously_invalid_symbols(
    tmp_path: Path,
) -> None:
    class FakeBinance:
        def __init__(self) -> None:
            self.symbols: list[str] = []

        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1h",
            limit: int = 80,
            start_time: int | None = None,
            end_time: int | None = None,
        ) -> list[dict[str, Any]]:
            if symbol in {"REQUIREDUSDT", "OCCUSDT", "AFSLUSDT"}:
                raise AssertionError(f"invalid symbol should not be fetched: {symbol}")
            self.symbols.append(symbol)
            return [
                {"open": 100, "high": 101, "low": 99, "close": 100, "open_time": 1},
                {"open": 100, "high": 112, "low": 97, "close": 108, "open_time": 2},
            ]

    fake_binance = FakeBinance()
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db")),
        binance=fake_binance,
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/mixed-labels",
        title=(
            "Binance Will List ACME (ACME), REQUIRED (REQUIRED), "
            "OCC (OCC), and AFSL (AFSL)"
        ),
        raw_text=(
            "Trading will open for ACME/USDT, REQUIRED/USDT, "
            "OCC/USDT, and AFSL/USDT spot trading pairs."
        ),
    )
    extracted = service.extract_events_from_snapshot(snapshot["snapshot_id"])
    event_id = extracted["event_ids"][0]
    for symbol in ["REQUIREDUSDT", "OCCUSDT", "AFSLUSDT"]:
        service.repository.mark_event_symbol_validity(
            event_id=event_id,
            symbol=symbol,
            status="invalid",
            reason="binance_spot_invalid_symbol",
        )
    _age_events(service, hours=48)

    result = asyncio.run(service.label_due_outcomes())

    assert result["status"] == "ok"
    assert result["labeled"] == 3
    assert result["skipped_invalid_symbols"] == 3
    assert set(fake_binance.symbols) == {"ACMEUSDT"}


def test_recent_event_outcome_labeling_is_skipped(tmp_path: Path) -> None:
    class FakeBinance:
        async def fetch_klines(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("recent events should not fetch klines")

    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db")),
        binance=FakeBinance(),
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME). Trading will open for ACME/USDT.",
    )
    service.extract_events_from_snapshot(snapshot["snapshot_id"])

    result = asyncio.run(service.label_due_outcomes())

    assert result["labeled"] == 0
    assert result["skipped_recent"] == 4


def test_outcome_labeling_uses_event_time_not_detected_at(tmp_path: Path) -> None:
    class FakeBinance:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1h",
            limit: int = 80,
            start_time: int | None = None,
            end_time: int | None = None,
        ) -> list[dict[str, Any]]:
            self.calls.append({"start_time": start_time, "end_time": end_time})
            return [
                {"open": 100, "high": 110, "low": 95, "close": 104, "open_time": 1},
                {"open": 104, "high": 112, "low": 102, "close": 108, "open_time": 2},
            ]

    fake_binance = FakeBinance()
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(db_path=str(tmp_path / "crypto_alpha.db")),
        binance=fake_binance,
    )
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME). Trading will open for ACME/USDT.",
    )
    service.extract_events_from_snapshot(snapshot["snapshot_id"])
    event_time = datetime.now(timezone.utc) - timedelta(hours=48)
    detected_at = datetime.now(timezone.utc)
    with service.repository._connect() as conn:
        conn.execute(
            "UPDATE crypto_alpha_events SET event_time=?, detected_at=?",
            (event_time.isoformat(), detected_at.isoformat()),
        )

    result = asyncio.run(service.label_due_outcomes())

    assert result["labeled"] == 3
    assert fake_binance.calls[0]["start_time"] == int(event_time.timestamp() * 1000)


def test_context_pack_stays_compact_with_many_events(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=str(tmp_path / "crypto_alpha.db"),
            context_limit=7,
        )
    )
    for idx in range(30):
        snapshot = service.store_snapshot(
            source_id="binance_announcements",
            url=f"https://www.binance.com/en/support/announcement/acme-{idx}",
            title=f"Binance Will List ACME (ACME) #{idx}",
            raw_text=f"Binance Will List ACME (ACME). ACME/USDT catalyst #{idx}.",
        )
        service.extract_events_from_snapshot(snapshot["snapshot_id"])

    pack = service.context_pack(symbols=["ACMEUSDT"], limit=7)

    assert pack["event_count"] == 7
    assert len(json.dumps(pack, ensure_ascii=False)) < 12000
    assert all("raw_text" not in item for item in pack["events"])
    assert "active_lessons" in pack
    assert "data_gaps" in pack


def test_context_pack_clamps_large_requested_limit(tmp_path: Path) -> None:
    service = CryptoAlphaService(
        config=CryptoAlphaConfig(
            db_path=str(tmp_path / "crypto_alpha.db"),
            context_limit=5,
        )
    )
    for idx in range(12):
        snapshot = service.store_snapshot(
            source_id="binance_announcements",
            url=f"https://www.binance.com/en/support/announcement/acme-{idx}",
            title=f"Binance Will List ACME (ACME) #{idx}",
            raw_text=f"Binance Will List ACME (ACME). ACME/USDT catalyst #{idx}.",
        )
        service.extract_events_from_snapshot(snapshot["snapshot_id"])

    pack = service.context_pack(symbols=["ACMEUSDT"], limit=100)

    assert pack["event_count"] == 5
    assert pack["limit"] == 5


def test_crypto_alpha_partial_schema_migrates(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto_alpha.db"
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_alpha_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_hash TEXT NOT NULL
            )
            """
        )

    service = CryptoAlphaService(config=CryptoAlphaConfig(db_path=str(db_path)))
    snapshot = service.store_snapshot(
        source_id="binance_announcements",
        url="https://www.binance.com/en/support/announcement/acme",
        title="Binance Will List ACME (ACME)",
        raw_text="Binance Will List ACME (ACME). Trading will open for ACME/USDT.",
    )

    assert snapshot["snapshot_id"] == 1


def test_collect_once_returns_planned_shape_without_outcome_labeling(tmp_path: Path) -> None:
    class FetchingService(CryptoAlphaService):
        async def fetch_source_snapshot(
            self,
            *,
            source_id: str,
            url: str,
            title: str = "",
            timeout_sec: float = 12.0,
        ) -> dict[str, Any]:
            return self.store_snapshot(
                source_id=source_id,
                url=url,
                title="Binance Will List ACME (ACME)",
                raw_text="Binance Will List ACME (ACME). ACME/USDT catalyst.",
            )

    service = FetchingService(
        config=CryptoAlphaConfig(
            db_path=str(tmp_path / "crypto_alpha.db"),
            source_ids="binance_announcements",
        )
    )

    result = asyncio.run(service.collect_once())

    assert result["status"] == "ok"
    assert result["sources"] == ["binance_announcements"]
    assert result["created_snapshots"] == 1
    assert result["created_events"] == 1
    assert "outcomes" not in result
