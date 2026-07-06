from __future__ import annotations

import base64
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradecraft.services.crypto_quant import (
    CryptoQuantConfig,
    CryptoQuantEngine,
    CryptoQuantOutcomeLabeler,
    CryptoQuantRepository,
)


def _decode_gzip_base64(value: str) -> str:
    assert value.startswith("gzip+base64:")
    return gzip.decompress(base64.b64decode(value.removeprefix("gzip+base64:"))).decode(
        "utf-8"
    )


def _bars(closes: list[float]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for index, close in enumerate(closes):
        volume = 1000.0 + index * 100.0
        rows.append(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": volume,
                "quote_volume": volume * close,
            }
        )
    return rows


def test_quant_config_defaults_are_compact() -> None:
    config = CryptoQuantConfig()

    assert config.db_path == ".runtime/crypto_quant.db"
    assert config.enabled is True
    assert config.context_limit == 16
    assert config.horizons == ("scalp", "intraday", "swing")


def test_quant_repository_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))

    with repo._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_quant_repository_saves_latest_signal(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))

    repo.save_signal(
        {
            "symbol": "BNBUSDT",
            "horizon": "intraday",
            "long_score": 64.2,
            "short_score": 28.4,
            "no_trade_score": 36.0,
            "expected_r_long": 0.42,
            "expected_r_short": -0.18,
            "signal_json": {
                "symbol": "BNBUSDT",
                "bias": "long",
                "drivers": ["multi-timeframe momentum is positive"],
                "risks": ["spread widened above normal"],
            },
            "updated_at": "2026-05-24T09:00:00+00:00",
        }
    )

    latest = repo.latest_signals(symbols=["BNBUSDT"], limit=5)

    assert len(latest) == 1
    assert latest[0]["symbol"] == "BNBUSDT"
    assert latest[0]["horizon"] == "intraday"
    assert latest[0]["long_score"] == 64.2
    assert latest[0]["short_score"] == 28.4
    assert latest[0]["no_trade_score"] == 36.0
    assert latest[0]["signal"]["bias"] == "long"


def test_crypto_quant_repository_exposes_evidence(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    repo.save_signal(
        {
            "symbol": "BTCUSDT",
            "horizon": "intraday",
            "long_score": 80,
            "short_score": 20,
            "no_trade_score": 10,
            "expected_r_long": 0.8,
            "expected_r_short": -0.2,
            "signal_json": {"bias": "long"},
            "updated_at": "2026-05-24T09:00:00+00:00",
        }
    )

    evidence = repo.latest_evidence(symbols=["BTCUSDT"], limit=5)

    assert evidence[0]["source"] == "crypto_quant"
    assert evidence[0]["signal_type"] == "directional_quant"
    assert evidence[0]["payload"]["bias"] == "long"


def test_quant_repository_keeps_signal_history_for_jue_retrieval(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    for index, long_score in enumerate([42.0, 55.0, 68.0]):
        repo.save_signal(
            {
                "symbol": "BNBUSDT",
                "horizon": "intraday",
                "long_score": long_score,
                "short_score": 70.0 - long_score,
                "no_trade_score": 35.0,
                "expected_r_long": (long_score - 35.0) / 100.0,
                "expected_r_short": ((70.0 - long_score) - 35.0) / 100.0,
                "signal_json": {
                    "bias": "long" if long_score >= 55 else "short",
                    "metrics": {"atr_pct": 1.4 + index, "rsi": 45 + index},
                },
                "updated_at": f"2026-05-24T09:0{index}:00+00:00",
            }
        )

    latest = repo.latest_signals(symbols=["BNBUSDT"], limit=1)
    history = repo.signal_history(symbol="BNBUSDT", horizon="intraday", limit=10)
    context = repo.retrieval_context(
        symbols=["BNBUSDT"], horizon="intraday", points_per_symbol=3
    )

    assert len(latest) == 1
    assert latest[0]["long_score"] == 68.0
    assert len(history) == 3
    assert history[0]["captured_at"] == "2026-05-24T09:02:00+00:00"
    assert context["items"][0]["history_points"] == 3
    assert context["items"][0]["trend"]["long_score_delta"] == 26.0
    assert context["items"][0]["recent_biases"] == ["long", "long", "short"]


def test_quant_repository_prunes_signal_history_windows_per_symbol_horizon(
    tmp_path: Path,
) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    base_time = datetime.now(timezone.utc)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(5):
            repo.save_signal(
                {
                    "symbol": symbol,
                    "horizon": "intraday",
                    "long_score": 40 + index,
                    "short_score": 30,
                    "no_trade_score": 20,
                    "signal_json": {"bias": "long", "index": index},
                    "updated_at": (base_time + timedelta(seconds=index)).isoformat(),
                }
            )
    for index in range(4):
        repo.save_signal(
            {
                "symbol": "BTCUSDT",
                "horizon": "swing",
                "long_score": 50 + index,
                "short_score": 20,
                "no_trade_score": 20,
                "signal_json": {"bias": "long", "index": index},
                "updated_at": (base_time + timedelta(seconds=index)).isoformat(),
            }
        )

    result = repo.prune_history(
        retention_days=7,
        archive_retention_days=14,
        hot_window_rows=2,
    )

    assert result["history_window"]["deleted"] == 8
    with sqlite3.connect(tmp_path / "quant.db") as conn:
        rows = conn.execute(
            """
            SELECT symbol, horizon, long_score
            FROM crypto_quant_signal_history
            ORDER BY symbol, horizon, long_score
            """
        ).fetchall()

    assert rows == [
        ("BTCUSDT", "intraday", 43.0),
        ("BTCUSDT", "intraday", 44.0),
        ("BTCUSDT", "swing", 52.0),
        ("BTCUSDT", "swing", 53.0),
        ("ETHUSDT", "intraday", 43.0),
        ("ETHUSDT", "intraday", 44.0),
    ]


def test_quant_repository_prunes_archive_history_windows_per_symbol_horizon(
    tmp_path: Path,
) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    base_time = datetime.now(timezone.utc) - timedelta(days=5)
    for index in range(5):
        repo.save_signal(
            {
                "symbol": "BTCUSDT",
                "horizon": "intraday",
                "long_score": 40 + index,
                "short_score": 30,
                "no_trade_score": 20,
                "signal_json": {"bias": "long", "index": index},
                "updated_at": (base_time + timedelta(seconds=index)).isoformat(),
            }
        )
    for index in range(4):
        repo.save_signal(
            {
                "symbol": "BTCUSDT",
                "horizon": "swing",
                "long_score": 50 + index,
                "short_score": 20,
                "no_trade_score": 20,
                "signal_json": {"bias": "long", "index": index},
                "updated_at": (base_time + timedelta(seconds=index)).isoformat(),
            }
        )

    result = repo.prune_history(
        retention_days=3,
        archive_retention_days=7,
        archive_window_rows=2,
    )

    assert result["archive_window"]["deleted"] == 5
    with sqlite3.connect(tmp_path / "quant.db") as conn:
        rows = conn.execute(
            """
            SELECT symbol, horizon, long_score
            FROM crypto_quant_signal_history_archive
            ORDER BY symbol, horizon, long_score
            """
        ).fetchall()

    assert rows == [
        ("BTCUSDT", "intraday", 43.0),
        ("BTCUSDT", "intraday", 44.0),
        ("BTCUSDT", "swing", 52.0),
        ("BTCUSDT", "swing", 53.0),
    ]


def test_quant_repository_prunes_old_history(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    repo.save_signal(
        {
            "symbol": "OLDUSDT",
            "horizon": "intraday",
            "long_score": 10,
            "short_score": 20,
            "no_trade_score": 80,
            "signal_json": {
                "bias": "no_trade",
                "metrics": {"atr_pct": 3.2, "rsi": 38.0},
                "raw_context": "old_quant_signal" * 100,
            },
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
    )
    repo.save_outcome(
        {
            "symbol": "OLDUSDT",
            "side": "long",
            "horizon": "1h",
            "outcome": "stop_first",
            "payload": {"raw_context": "old_quant_outcome" * 100},
            "labeled_at": "2020-01-01T00:00:00+00:00",
        }
    )

    result = repo.prune_history(retention_days=30)

    assert result["status"] == "ok"
    assert result["history_deleted"] == 1
    assert result["outcomes_deleted"] == 1
    assert result["archived"]["crypto_quant_signal_history"] == 1
    assert result["archived"]["crypto_quant_outcomes"] == 1
    with sqlite3.connect(tmp_path / "quant.db") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_quant_signal_history_archive"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_quant_outcomes_archive"
        ).fetchone()[0] == 1
        archived_signal_json = conn.execute(
            "SELECT signal_json FROM crypto_quant_signal_history_archive"
        ).fetchone()[0]
        archived_outcome_json = conn.execute(
            "SELECT payload_json FROM crypto_quant_outcomes_archive"
        ).fetchone()[0]
        archived_signal = json.loads(_decode_gzip_base64(archived_signal_json))
        archived_outcome = json.loads(_decode_gzip_base64(archived_outcome_json))
        assert archived_signal["raw_context"] == "old_quant_signal" * 100
        assert archived_outcome["payload"]["raw_context"] == (
            "old_quant_outcome" * 100
        )
        history_retention = result["retention"]["tables"][
            "crypto_quant_signal_history"
        ]
        outcome_retention = result["retention"]["tables"]["crypto_quant_outcomes"]
        assert history_retention["compressed_columns"] == ["signal_json"]
        assert outcome_retention["compressed_columns"] == ["payload_json"]


def test_quant_repository_prunes_archive_tables_when_enabled(tmp_path: Path) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    repo.save_signal(
        {
            "symbol": "OLDUSDT",
            "horizon": "intraday",
            "long_score": 10,
            "short_score": 20,
            "no_trade_score": 80,
            "signal_json": {"bias": "no_trade"},
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
    )
    repo.save_outcome(
        {
            "symbol": "OLDUSDT",
            "side": "long",
            "horizon": "1h",
            "outcome": "stop_first",
            "payload": {"raw_context": "old_quant_outcome"},
            "labeled_at": "2020-01-01T00:00:00+00:00",
        }
    )

    result = repo.prune_history(retention_days=30, archive_retention_days=30)

    assert result["archived"]["crypto_quant_signal_history"] == 1
    assert result["archived"]["crypto_quant_outcomes"] == 1
    assert result["archive_deleted"]["crypto_quant_signal_history_archive"] == 1
    assert result["archive_deleted"]["crypto_quant_outcomes_archive"] == 1
    assert result["archive_retention"]["vacuumed"] is True
    with sqlite3.connect(tmp_path / "quant.db") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_quant_signal_history_archive"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_quant_outcomes_archive"
        ).fetchone()[0] == 0


def test_quant_repository_tiered_retention_deletes_cold_and_archives_warm(
    tmp_path: Path,
) -> None:
    repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    now = datetime.now(timezone.utc)
    rows = [
        ("COLDUSDT", now - timedelta(days=20)),
        ("WARMUSDT", now - timedelta(days=10)),
        ("HOTUSDT", now - timedelta(days=1)),
    ]
    for symbol, timestamp in rows:
        repo.save_signal(
            {
                "symbol": symbol,
                "horizon": "intraday",
                "long_score": 10,
                "short_score": 20,
                "no_trade_score": 80,
                "signal_json": {"bias": "no_trade", "symbol": symbol},
                "updated_at": timestamp.isoformat(),
            }
        )
        repo.save_outcome(
            {
                "symbol": symbol,
                "side": "long",
                "horizon": "1h",
                "outcome": "stop_first",
                "payload": {"symbol": symbol},
                "labeled_at": timestamp.isoformat(),
            }
        )

    result = repo.prune_history(retention_days=7, archive_retention_days=14)

    assert result["cold_deleted"]["crypto_quant_signal_history"] == 1
    assert result["cold_deleted"]["crypto_quant_outcomes"] == 1
    assert result["archived"]["crypto_quant_signal_history"] == 1
    assert result["archived"]["crypto_quant_outcomes"] == 1
    with sqlite3.connect(tmp_path / "quant.db") as conn:
        hot_history = conn.execute(
            "SELECT symbol FROM crypto_quant_signal_history ORDER BY symbol"
        ).fetchall()
        warm_archive = conn.execute(
            "SELECT symbol FROM crypto_quant_signal_history_archive ORDER BY symbol"
        ).fetchall()
        hot_outcomes = conn.execute(
            "SELECT symbol FROM crypto_quant_outcomes ORDER BY symbol"
        ).fetchall()
        warm_outcomes = conn.execute(
            "SELECT symbol FROM crypto_quant_outcomes_archive ORDER BY symbol"
        ).fetchall()
    assert hot_history == [("HOTUSDT",)]
    assert warm_archive == [("WARMUSDT",)]
    assert hot_outcomes == [("HOTUSDT",)]
    assert warm_outcomes == [("WARMUSDT",)]


def test_quant_engine_scores_long_when_trend_and_volume_confirm() -> None:
    engine = CryptoQuantEngine()
    signal = engine.build_signal(
        symbol="TESTUSDT",
        horizon="intraday",
        klines_by_interval={
            "5m": _bars([100, 101, 102, 103, 104, 105, 106, 107, 109, 111]),
            "15m": _bars([100, 101, 102, 103, 104, 106, 108, 110, 112, 114]),
            "1h": _bars([100, 102, 103, 104, 106, 108, 110, 112, 115, 118]),
        },
        market_features={
            "spread_bps": 1.2,
            "funding_rate": 0.00005,
            "mark_index_basis_pct": 0.03,
            "open_interest": 2_000_000,
            "timeframe_alignment": "bullish",
        },
        btc_closes=[100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        eth_closes=[100, 100.5, 101, 102, 103, 104, 105, 106, 107, 108],
    )

    assert signal["bias"] == "long"
    assert signal["long_score"] > signal["short_score"]
    assert signal["long_score"] >= 60
    assert signal["no_trade_score"] < 50
    assert signal["metrics"]["atr_pct"] > 0
    assert signal["metrics"]["rsi"] >= 50


def test_quant_engine_raises_no_trade_when_spread_and_extension_are_high() -> None:
    engine = CryptoQuantEngine()
    signal = engine.build_signal(
        symbol="WIDEUSDT",
        horizon="intraday",
        klines_by_interval={
            "5m": _bars([100, 104, 109, 116, 124, 133, 143, 154, 166, 179]),
            "15m": _bars([100, 105, 111, 118, 126, 135, 145, 156, 168, 181]),
        },
        market_features={
            "spread_bps": 18.0,
            "funding_rate": 0.0009,
            "mark_index_basis_pct": 0.20,
            "open_interest": 10_000_000,
            "timeframe_alignment": "bullish",
        },
        btc_closes=[100, 101, 102, 102, 103, 103, 104, 104, 105, 105],
        eth_closes=[100, 101, 101, 102, 102, 103, 103, 104, 104, 105],
    )

    assert signal["bias"] == "no_trade"
    assert signal["no_trade_score"] >= 60
    assert "spread is expensive" in signal["risks"]


def test_outcome_labeler_detects_target_before_stop_for_long() -> None:
    labeler = CryptoQuantOutcomeLabeler()

    label = labeler.label_path(
        symbol="BTCUSDT",
        side="long",
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        rows=[
            {"high": 103.0, "low": 99.0, "close": 102.0},
            {"high": 111.0, "low": 101.0, "close": 110.5},
            {"high": 112.0, "low": 94.0, "close": 96.0},
        ],
        horizon="1h",
    )

    assert label["outcome"] == "target_first"
    assert label["r_multiple"] == 2.0
    assert label["mfe_r"] >= 2.0
    assert label["mae_r"] > -1.0


def test_outcome_labeler_detects_stop_before_target_for_short() -> None:
    labeler = CryptoQuantOutcomeLabeler()

    label = labeler.label_path(
        symbol="BNBUSDT",
        side="short",
        entry_price=100.0,
        stop_price=105.0,
        target_price=90.0,
        rows=[
            {"high": 106.0, "low": 98.0, "close": 105.5},
            {"high": 107.0, "low": 89.0, "close": 91.0},
        ],
        horizon="1h",
    )

    assert label["outcome"] == "stop_first"
    assert label["r_multiple"] == -1.0
    assert label["mae_r"] <= -1.0
