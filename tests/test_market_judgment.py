from __future__ import annotations

import asyncio
import base64
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradecraft.services.investment_memory import (
    InvestmentMemoryConfig,
    InvestmentMemoryService,
)
from tradecraft.services.market_judgment import (
    MarketJudgmentConfig,
    MarketJudgmentEngine,
    MarketJudgmentRepository,
    MarketQuoteService,
    build_market_clock,
    next_krx_decision_due_at,
    normalize_account_assets,
    normalize_kis_quote,
)
from tradecraft.services.market_pulse import MarketPulseConfig, MarketPulseService


def _decode_gzip_base64(value: str) -> str:
    assert value.startswith("gzip+base64:")
    return gzip.decompress(base64.b64decode(value.removeprefix("gzip+base64:"))).decode(
        "utf-8"
    )


def test_market_judgment_repository_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")

    with repository._connect() as conn:
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout_ms >= 30000


def test_market_quote_service_defaults_to_no_naver_fallback() -> None:
    service = MarketQuoteService(_FakeKIS())

    assert service.use_naver_fallback is False


class _OpenCalendar:
    def is_open_day(self, value) -> bool:
        _ = value
        return True


class _FakeKIS:
    async def fetch_balance_assets(self) -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "value_krw": 1_000_000.0,
                "qty": 1_000_000.0,
            },
            {
                "asset": "005930",
                "asset_name": "삼성전자",
                "kind": "position",
                "qty": 10.0,
                "available": 8.0,
                "avg_price": 80000.0,
                "mark_price": 76000.0,
                "value_krw": 760000.0,
                "pnl_krw": -40000.0,
            },
        ]

    async def fetch_domestic_quote(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "name": "삼성전자" if symbol == "005930" else symbol,
            "price": 76000.0,
            "raw": {
                "stck_prpr": "76000",
                "prdy_vrss": "-1200",
                "prdy_ctrt": "-1.55",
                "stck_oprc": "77000",
                "stck_hgpr": "78000",
                "stck_lwpr": "75000",
                "acml_vol": "1234567",
                "acml_tr_pbmn": "90000000000",
                "hts_kor_isnm": "삼성전자" if symbol == "005930" else symbol,
            },
        }


class _FailingKIS(_FakeKIS):
    async def fetch_balance_assets(self) -> list[dict]:
        raise RuntimeError("token rate limited")


class _RecordingKIS(_FakeKIS):
    def __init__(self) -> None:
        self.quoted_symbols: list[str] = []

    async def fetch_domestic_quote(self, symbol: str) -> dict:
        self.quoted_symbols.append(symbol)
        return await super().fetch_domestic_quote(symbol)


class _FakeStrategy:
    def build_candidates(self, *, query, research_feed, limit=None) -> dict:
        _ = (query, research_feed, limit)
        return {
            "status": "ok",
            "score_method_version": "v2",
            "candidates": [
                {
                    "symbol": "000660",
                    "name": "SK하이닉스",
                    "score": 76,
                    "confidence": 68,
                    "reasons": ["HBM 수요"],
                    "risks": ["변동성"],
                }
            ],
            "exclusions": [],
            "sources": [],
        }

    def list_external_signals(self, **kwargs) -> dict:
        _ = kwargs
        return {"status": "ok", "items": [{"symbol": "402340"}]}


class _FakeReportRepository:
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def search(self, **kwargs) -> list[dict]:
        _ = kwargs
        return []

    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        return {symbol: self.names[symbol] for symbol in symbols if symbol in self.names}


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self) -> None:
        self.last_payload: dict | None = None

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.last_payload = payload
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "judgments": [
                        {
                            "symbol": "005930",
                            "stance": "risk_check",
                            "account_action": "risk_check",
                            "horizon": "short_term",
                            "confidence": 0.73,
                            "reasons": ["국장1 보유 손익과 장중 약세를 함께 확인"],
                            "risks": ["추가 하락 시나리오"],
                            "triggers": ["거래대금 확인"],
                            "data_gaps": ["고래"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        }


class _FailingLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self, error: str = "llm down") -> None:
        self.error = error
        self.last_payload: dict | None = None

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.last_payload = payload
        return {"ok": False, "error": self.error}


class _ResearchRunnerGapLLM(_FakeLLM):
    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.last_payload = payload
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "judgments": [
                        {
                            "symbol": "005930",
                            "stance": "risk_check",
                            "account_action": "risk_check",
                            "horizon": "short_term",
                            "confidence": 0.73,
                            "reasons": ["국장1 보유 손익과 장중 약세를 함께 확인"],
                            "risks": ["추가 하락 시나리오"],
                            "triggers": ["거래대금 확인"],
                            "data_gaps": [
                                "Research Runner 데이터 없음",
                                "리서치 러너 자료 없음",
                                "고래",
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        }


class _MissingValidationRepairResolutionLLM(_FakeLLM):
    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = timeout_ms
        self.last_payload = payload
        return {
            "ok": True,
            "content": json.dumps(
                {
                    "judgments": [
                        {
                            "symbol": "005930",
                            "stance": "risk_check",
                            "account_action": "risk_check",
                            "horizon": "short_term",
                            "confidence": 0.73,
                            "reasons": ["위키 검증 계약이 있으나 해소 기록을 누락"],
                            "risks": ["학습 루프 미기록"],
                            "triggers": ["거래대금 확인"],
                            "data_gaps": [],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        }


def test_latest_quotes_filters_requested_symbols(tmp_path: Path) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    repository.save_quotes(
        [
            {
                "symbol": "001740",
                "name": "SK네트웍스",
                "price": 5500,
                "source": "kis",
                "fetched_at": "2026-06-12T00:00:00+00:00",
                "status": "ok",
            },
            {
                "symbol": "005930",
                "name": "삼성전자",
                "price": 76000,
                "source": "kis",
                "fetched_at": "2026-06-12T00:01:00+00:00",
                "status": "ok",
            },
            {
                "symbol": "000660",
                "name": "SK하이닉스",
                "price": 210000,
                "source": "kis",
                "fetched_at": "2026-06-12T00:02:00+00:00",
                "status": "ok",
            },
        ]
    )

    payload = repository.latest_quotes(limit=100, symbols=["005930"])

    assert payload["count"] == 1
    assert payload["items"][0]["symbol"] == "005930"
    assert payload["items"][0]["name"] == "삼성전자"


def test_market_judgment_compacts_large_quote_raw_payloads(tmp_path: Path) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    repository.save_quotes(
        [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "price": 76000,
                "source": "kis",
                "fetched_at": "2026-06-12T00:01:00+00:00",
                "status": "ok",
                "raw": {
                    "stck_prpr": "76000",
                    "prdy_vrss": "1000",
                    "prdy_ctrt": "1.33",
                    "acml_vol": "123456",
                    "hts_kor_isnm": "삼성전자",
                    "large_unused_blob": "x" * 5000,
                },
            }
        ]
    )

    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        raw = json.loads(
            conn.execute("SELECT raw_json FROM quote_snapshots").fetchone()[0]
        )

    assert raw["stck_prpr"] == "76000"
    assert raw["acml_vol"] == "123456"
    assert raw["_raw_compacted"] is True
    assert raw["_raw_key_count"] == 6
    assert "large_unused_blob" not in raw


def test_market_judgment_prune_history_deletes_old_quote_snapshots(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    repository.save_quotes(
        [
            {
                "symbol": "005930",
                "price": 76000,
                "fetched_at": (now - timedelta(days=10)).isoformat(),
                "raw": {"source": "old_quote"},
            },
            {
                "symbol": "000660",
                "price": 210000,
                "fetched_at": now.isoformat(),
            },
        ]
    )

    result = repository.prune_history(retention_days=7, now=now)

    assert result["quote_snapshots_deleted"] == 1
    assert result["archived"]["quote_snapshots"] == 1
    payload = repository.latest_quotes(limit=10)
    assert [row["symbol"] for row in payload["items"]] == ["000660"]
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        archive_row = conn.execute(
            "SELECT symbol, price, raw_json FROM quote_snapshots_archive"
        ).fetchone()
        assert archive_row[:2] == ("005930", 76000)
        assert json.loads(_decode_gzip_base64(archive_row[2])) == {
            "source": "old_quote"
        }


def test_market_judgment_prune_history_prunes_old_quote_archive(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    repository.save_quotes(
        [
            {
                "symbol": "005930",
                "price": 76000,
                "fetched_at": (now - timedelta(days=20)).isoformat(),
                "raw": {"source": "cold_quote"},
            },
            {
                "symbol": "000660",
                "price": 210000,
                "fetched_at": (now - timedelta(days=10)).isoformat(),
                "raw": {"source": "warm_quote"},
            },
            {
                "symbol": "035420",
                "price": 170000,
                "fetched_at": now.isoformat(),
            },
        ]
    )

    result = repository.prune_history(
        retention_days=7,
        quote_archive_retention_days=14,
        now=now,
    )

    assert result["quote_snapshots_deleted"] == 2
    assert result["archived"]["quote_snapshots"] == 2
    assert result["archive_deleted"]["quote_snapshots_archive"] == 1
    assert result["vacuumed"] is True
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        archived_symbols = {
            row[0]
            for row in conn.execute(
                "SELECT symbol FROM quote_snapshots_archive ORDER BY symbol"
            )
        }
    assert archived_symbols == {"000660"}


def test_market_judgment_engine_prune_history_accepts_quote_archive_retention_days(
    tmp_path: Path,
) -> None:
    class RecordingRepository:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def prune_history(self, **kwargs) -> dict[str, object]:
            self.kwargs = kwargs
            return {"status": "ok", "received": kwargs}

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )
    repository = RecordingRepository()
    engine.repository = repository  # type: ignore[assignment]

    result = engine.prune_history(
        retention_days=7,
        quote_archive_retention_days=14,
        account_retention_days=30,
        judgment_retention_days=45,
        judgment_archive_retention_days=21,
    )

    assert result["status"] == "ok"
    assert repository.kwargs == {
        "retention_days": 7,
        "quote_archive_retention_days": 14,
        "account_retention_days": 30,
        "judgment_retention_days": 45,
        "judgment_archive_retention_days": 21,
        "compact_recent_run_count": 96,
        "compact_min_chars": 20_000,
        "compact_symbol_min_chars": 2_000,
    }


def test_market_judgment_prune_history_deletes_old_accounts_and_judgments(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    old_at = (now - timedelta(days=40)).isoformat()
    recent_at = now.isoformat()
    repository.save_account(
        {
            "captured_at": old_at,
            "status": "ok",
            "cash_krw": 1_000,
            "position_count": 0,
            "raw_marker": "old_account",
        }
    )
    repository.save_account(
        {
            "captured_at": recent_at,
            "status": "ok",
            "cash_krw": 2_000,
            "position_count": 0,
        }
    )
    old_run_id = repository.save_judgment_run(
        run={
            "run_at": old_at,
            "status": "ok",
            "prompt": {"large": "x" * 1000},
            "response": {"raw": "old_response" * 100},
            "source_snapshot": {"raw": "old_source" * 100},
        },
        judgments=[
            {
                "symbol": "005930",
                "stance": "watch",
                "reasons": ["old_reason" * 50],
                "risks": ["old_risk" * 50],
                "triggers": ["old_trigger" * 50],
                "data_gaps": ["old_gap" * 50],
                "quote": {"raw": "old_quote" * 100},
                "position": {"raw": "old_position" * 100},
                "strategy": {"raw": "old_strategy" * 100},
            }
        ],
    )
    recent_run_id = repository.save_judgment_run(
        run={
            "run_at": recent_at,
            "status": "ok",
            "prompt": {"large": "y" * 1000},
        },
        judgments=[{"symbol": "000660", "stance": "watch"}],
    )

    result = repository.prune_history(
        retention_days=7,
        account_retention_days=30,
        judgment_retention_days=30,
        now=now,
    )

    assert result["account_snapshots_deleted"] == 1
    assert result["judgment_runs_deleted"] == 1
    assert result["symbol_judgments_deleted"] == 1
    assert result["archived"]["account_snapshots"] == 1
    assert result["archived"]["judgment_runs"] == 1
    assert result["archived"]["symbol_judgments"] == 1
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0] == 1
        archived_account = conn.execute(
            "SELECT cash_krw, raw_json FROM account_snapshots_archive"
        ).fetchone()
        assert archived_account[0] == 1_000
        assert json.loads(_decode_gzip_base64(archived_account[1]))["raw_marker"] == (
            "old_account"
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM judgment_runs WHERE id = ?",
                (old_run_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM judgment_runs WHERE id = ?",
                (recent_run_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM judgment_runs_archive WHERE id = ?",
                (old_run_id,),
            ).fetchone()[0]
            == 1
        )
        archived_run = conn.execute(
            """
            SELECT prompt_json, response_json, source_snapshot_json
            FROM judgment_runs_archive
            WHERE id = ?
            """,
            (old_run_id,),
        ).fetchone()
        assert json.loads(_decode_gzip_base64(archived_run[0])) == {
            "large": "x" * 1000
        }
        assert json.loads(_decode_gzip_base64(archived_run[1])) == {
            "raw": "old_response" * 100
        }
        assert json.loads(_decode_gzip_base64(archived_run[2])) == {
            "raw": "old_source" * 100
        }
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM symbol_judgments_archive WHERE run_id = ?",
                (old_run_id,),
            ).fetchone()[0]
            == 1
        )
        archived_symbol = conn.execute(
            """
            SELECT reasons_json, risks_json, triggers_json, data_gaps_json,
                   quote_json, position_json, strategy_json
            FROM symbol_judgments_archive
            WHERE run_id = ?
            """,
            (old_run_id,),
        ).fetchone()
        assert json.loads(_decode_gzip_base64(archived_symbol[0])) == [
            "old_reason" * 50
        ]
        assert json.loads(_decode_gzip_base64(archived_symbol[1])) == [
            "old_risk" * 50
        ]
        assert json.loads(_decode_gzip_base64(archived_symbol[2])) == [
            "old_trigger" * 50
        ]
        assert json.loads(_decode_gzip_base64(archived_symbol[3])) == [
            "old_gap" * 50
        ]
        assert json.loads(_decode_gzip_base64(archived_symbol[4])) == {
            "raw": "old_quote" * 100
        }
        assert json.loads(_decode_gzip_base64(archived_symbol[5])) == {
            "raw": "old_position" * 100
        }
        assert json.loads(_decode_gzip_base64(archived_symbol[6])) == {
            "raw": "old_strategy" * 100
        }


def test_market_judgment_prune_history_prunes_old_judgment_archives(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    expired_at = (now - timedelta(days=50)).isoformat()
    retained_at = (now - timedelta(days=35)).isoformat()

    expired_run_id = repository.save_judgment_run(
        run={"run_at": expired_at, "status": "ok", "prompt": {"old": True}},
        judgments=[{"symbol": "005930", "stance": "watch"}],
    )
    retained_run_id = repository.save_judgment_run(
        run={"run_at": retained_at, "status": "ok", "prompt": {"kept": True}},
        judgments=[{"symbol": "000660", "stance": "watch"}],
    )

    result = repository.prune_history(
        retention_days=7,
        judgment_retention_days=30,
        judgment_archive_retention_days=14,
        now=now,
    )
    assert result["archived"]["judgment_runs"] == 2
    assert result["archived"]["symbol_judgments"] == 2
    assert result["archive_deleted"]["judgment_runs_archive"] == 1
    assert result["archive_deleted"]["symbol_judgments_archive"] == 1
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM judgment_runs_archive WHERE id = ?",
                (expired_run_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM symbol_judgments_archive WHERE run_id = ?",
                (expired_run_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM judgment_runs_archive WHERE id = ?",
                (retained_run_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM symbol_judgments_archive WHERE run_id = ?",
                (retained_run_id,),
            ).fetchone()[0]
            == 1
        )


def test_market_judgment_prune_history_compacts_old_run_payloads(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    old_run_id = repository.save_judgment_run(
        run={
            "run_at": (now - timedelta(hours=2)).isoformat(),
            "status": "ok",
            "prompt": {"large": "x" * 2000},
            "response": {"raw": "response" * 500},
            "source_snapshot": {"raw": "source" * 500},
        },
        judgments=[{"symbol": "005930", "stance": "watch", "reasons": ["kept"]}],
    )
    recent_run_id = repository.save_judgment_run(
        run={
            "run_at": now.isoformat(),
            "status": "ok",
            "prompt": {"large": "y" * 2000},
            "response": {"raw": "recent_response" * 500},
            "source_snapshot": {"raw": "recent_source" * 500},
        },
        judgments=[{"symbol": "000660", "stance": "watch", "reasons": ["recent"]}],
    )

    result = repository.prune_history(
        retention_days=7,
        judgment_retention_days=30,
        now=now,
        compact_recent_run_count=1,
        compact_min_chars=100,
    )

    assert result["compacted"]["judgment_runs"] == 1
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        old_row = conn.execute(
            """
            SELECT prompt_json, response_json, source_snapshot_json
            FROM judgment_runs
            WHERE id = ?
            """,
            (old_run_id,),
        ).fetchone()
        recent_row = conn.execute(
            """
            SELECT prompt_json, response_json, source_snapshot_json
            FROM judgment_runs
            WHERE id = ?
            """,
            (recent_run_id,),
        ).fetchone()
        symbol_count = conn.execute(
            "SELECT COUNT(*) FROM symbol_judgments WHERE run_id = ?",
            (old_run_id,),
        ).fetchone()[0]

    assert json.loads(old_row[0])["compacted"] is True
    assert json.loads(old_row[1])["compacted"] is True
    assert json.loads(old_row[2])["compacted"] is True
    assert json.loads(recent_row[0]) == {"large": "y" * 2000}
    assert symbol_count == 1


def test_market_judgment_prune_history_compacts_old_symbol_payloads(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    old_run_id = repository.save_judgment_run(
        run={
            "run_at": (now - timedelta(hours=2)).isoformat(),
            "status": "ok",
            "prompt": {"small": True},
        },
        judgments=[
            {
                "symbol": "005930",
                "stance": "watch",
                "reasons": ["old"],
                "risks": ["old risk"],
                "triggers": ["old trigger"],
                "data_gaps": ["old gap"],
                "quote": {"raw": "quote" * 1000},
                "position": {"raw": "position" * 1000},
                "strategy": {"raw": "strategy" * 1000},
            }
        ],
    )
    recent_run_id = repository.save_judgment_run(
        run={
            "run_at": now.isoformat(),
            "status": "ok",
            "prompt": {"small": True},
        },
        judgments=[
            {
                "symbol": "000660",
                "stance": "watch",
                "quote": {"raw": "recent_quote" * 1000},
                "strategy": {"raw": "recent_strategy" * 1000},
            }
        ],
    )

    result = repository.prune_history(
        retention_days=7,
        judgment_retention_days=30,
        now=now,
        compact_recent_run_count=1,
        compact_min_chars=100,
    )

    assert result["compacted"]["symbol_judgments"] == 1
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        old_row = conn.execute(
            """
            SELECT reasons_json, risks_json, triggers_json, data_gaps_json,
                   quote_json, position_json, strategy_json
            FROM symbol_judgments
            WHERE run_id = ?
            """,
            (old_run_id,),
        ).fetchone()
        recent_row = conn.execute(
            """
            SELECT quote_json, strategy_json
            FROM symbol_judgments
            WHERE run_id = ?
            """,
            (recent_run_id,),
        ).fetchone()

    assert json.loads(old_row[0]) == ["old"]
    assert json.loads(old_row[1]) == ["old risk"]
    assert json.loads(old_row[2]) == ["old trigger"]
    assert json.loads(old_row[3]) == ["old gap"]
    assert json.loads(old_row[4])["compacted"] is True
    assert json.loads(old_row[5])["compacted"] is True
    assert json.loads(old_row[6])["compacted"] is True
    assert json.loads(recent_row[0]) == {"raw": "recent_quote" * 1000}
    assert json.loads(recent_row[1]) == {"raw": "recent_strategy" * 1000}


def test_market_judgment_prune_history_uses_lower_default_symbol_payload_threshold(
    tmp_path: Path,
) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    old_run_id = repository.save_judgment_run(
        run={
            "run_at": (now - timedelta(hours=2)).isoformat(),
            "status": "ok",
            "prompt": {"small": True},
        },
        judgments=[
            {
                "symbol": "005930",
                "stance": "watch",
                "quote": {"raw": "quote" * 350},
                "strategy": {"raw": "strategy" * 350},
            }
        ],
    )
    repository.save_judgment_run(
        run={
            "run_at": now.isoformat(),
            "status": "ok",
            "prompt": {"small": True},
        },
        judgments=[{"symbol": "000660", "stance": "watch"}],
    )

    result = repository.prune_history(
        retention_days=7,
        judgment_retention_days=30,
        now=now,
        compact_recent_run_count=1,
    )

    assert result["compacted"]["judgment_runs"] == 0
    assert result["compacted"]["symbol_judgments"] == 1
    with sqlite3.connect(str(tmp_path / "market_judgment.db")) as conn:
        old_row = conn.execute(
            "SELECT quote_json, strategy_json FROM symbol_judgments WHERE run_id = ?",
            (old_run_id,),
        ).fetchone()

    assert json.loads(old_row[0])["compacted"] is True
    assert json.loads(old_row[1])["compacted"] is True


def test_latest_quotes_enriches_symbol_names_from_report_directory(tmp_path: Path) -> None:
    repository = MarketJudgmentRepository(tmp_path / "market_judgment.db")
    repository.save_quotes(
        [
            {
                "symbol": "005930",
                "name": "005930",
                "price": 76000,
                "source": "kis",
                "fetched_at": "2026-06-12T00:01:00+00:00",
                "status": "ok",
            }
        ]
    )
    engine = object.__new__(MarketJudgmentEngine)
    engine.repository = repository
    engine.report_repository = _FakeReportRepository({"005930": "삼성전자"})

    payload = engine.latest_quotes(limit=10, symbols=["005930"])

    assert payload["items"][0]["name"] == "삼성전자"


@pytest.fixture
def memory_context_provider():
    def provider(symbols, quotes, account, strategy, market_pulse):
        _ = (symbols, quotes, account, strategy)
        return {
            "status": "ok",
            "persona": "쥬는 한국장 투자 파트너다.",
            "received_market_pulse_status": market_pulse.get("status"),
            "decision_skills": {
                "market_judge": {
                    "version": "jue.market_judge.v1",
                    "content_md": "# 쥬 장중 판단 스킬\n- 계좌와 마켓 펄스를 먼저 본다.",
                },
                "risk_manager": {
                    "version": "jue.risk_manager.v1",
                    "content_md": "# 쥬 리스크 매니저 스킬\n- 손절과 비중을 확인한다.",
                },
            },
            "decision_skill_status": {"count": 2, "missing": []},
        }

    return provider


def test_market_clock_sessions_respect_kst_open_window() -> None:
    clock = build_market_clock(
        now=datetime(2026, 5, 7, 9, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    assert clock["session"] == "regular"
    assert clock["is_market_open"] is True


def test_next_krx_decision_due_at_uses_pre_open_when_market_is_closed_before_open() -> None:
    clock = build_market_clock(
        now=datetime(2026, 6, 30, 7, 45, tzinfo=ZoneInfo("Asia/Seoul")),
        calendar=_OpenCalendar(),
    )

    due_at = next_krx_decision_due_at(clock, now=datetime(2026, 6, 30, 7, 45, tzinfo=ZoneInfo("Asia/Seoul")))

    assert due_at == "2026-06-30T08:30:00+09:00"


def test_next_krx_decision_due_at_can_skip_post_close_for_kis_manager() -> None:
    clock = build_market_clock(
        now=datetime(2026, 6, 30, 15, 25, tzinfo=ZoneInfo("Asia/Seoul")),
        calendar=_OpenCalendar(),
    )

    due_at = next_krx_decision_due_at(
        clock,
        now=datetime(2026, 6, 30, 15, 25, tzinfo=ZoneInfo("Asia/Seoul")),
        include_post_close=False,
    )

    assert due_at == "2026-07-01T08:30:00+09:00"


def test_normalize_account_assets_computes_position_weight_and_pnl_pct() -> None:
    payload = normalize_account_assets(
        [
            {"asset": "KRW", "kind": "cash", "value_krw": 1_000_000},
            {
                "asset": "005930",
                "asset_name": "삼성전자",
                "kind": "position",
                "qty": 10,
                "avg_price": 80000,
                "value_krw": 760000,
                "pnl_krw": -40000,
            },
        ]
    )

    position = payload["positions"][0]
    assert payload["cash_krw"] == pytest.approx(1_000_000)
    assert payload["settled_cash_krw"] == pytest.approx(1_000_000)
    assert payload["orderable_cash_krw"] == pytest.approx(1_000_000)
    assert position["unrealized_pnl_pct"] == pytest.approx(-5.0)
    assert position["position_weight"] == pytest.approx(760000 / 1760000)


def test_normalize_account_assets_keeps_cash_settlement_breakdown() -> None:
    payload = normalize_account_assets(
        [
            {
                "asset": "KRW",
                "kind": "cash",
                "value_krw": 4_264_740,
                "available": 4_264_740,
                "settled_cash_krw": 336_752,
                "orderable_cash_krw": 4_264_740,
                "receivable_cash_krw": 3_927_988,
                "settlement_cash_krw": 4_264_740,
                "today_sell_amount_krw": 3_936_000,
                "today_fee_tax_krw": 8_012,
            },
            {
                "asset": "089970",
                "asset_name": "브이엠",
                "kind": "position",
                "qty": 4,
                "avg_price": 56800,
                "value_krw": 233600,
                "pnl_krw": 6400,
            },
        ]
    )

    assert payload["cash_krw"] == pytest.approx(4_264_740)
    assert payload["settled_cash_krw"] == pytest.approx(336_752)
    assert payload["orderable_cash_krw"] == pytest.approx(4_264_740)
    assert payload["receivable_cash_krw"] == pytest.approx(3_927_988)
    assert payload["today_sell_amount_krw"] == pytest.approx(3_936_000)
    assert payload["today_fee_tax_krw"] == pytest.approx(8_012)
    assert payload["total_value_krw"] == pytest.approx(4_498_340)


def test_normalize_account_assets_prefers_broker_net_asset_for_total() -> None:
    payload = normalize_account_assets(
        [
            {
                "asset": "KRW",
                "kind": "cash",
                "value_krw": 4_132_803,
                "available": 4_132_803,
                "orderable_cash_krw": 4_132_803,
                "net_asset_krw": 4_476_840,
            },
            {
                "asset": "069500",
                "asset_name": "KODEX 200",
                "kind": "position",
                "qty": 1,
                "available": 1,
                "avg_price": 123_660,
                "value_krw": 123_350,
                "pnl_krw": -310,
            },
            {
                "asset": "091160",
                "asset_name": "KODEX 반도체",
                "kind": "position",
                "qty": 2,
                "available": 2,
                "avg_price": 141_510,
                "value_krw": 311_000,
                "pnl_krw": 27_980,
            },
        ]
    )

    assert payload["cash_krw"] == pytest.approx(4_132_803)
    assert payload["position_value_krw"] == pytest.approx(434_350)
    assert payload["computed_total_value_krw"] == pytest.approx(4_567_153)
    assert payload["broker_total_value_krw"] == pytest.approx(4_476_840)
    assert payload["total_value_krw"] == pytest.approx(4_476_840)
    assert payload["total_value_basis"] == "broker_net_asset"


def test_normalize_kis_quote_extracts_intraday_fields() -> None:
    quote = normalize_kis_quote(
        {
            "symbol": "005930",
            "name": "삼성전자",
            "price": 76000,
            "raw": {
                "stck_prpr": "76000",
                "prdy_vrss": "-1200",
                "prdy_ctrt": "-1.55",
                "acml_vol": "123",
            },
        },
        fetched_at="2026-05-07T00:00:00+00:00",
    )

    assert quote["price"] == pytest.approx(76000)
    assert quote["change_pct"] == pytest.approx(-1.55)
    assert quote["volume"] == pytest.approx(123)


def test_market_judgment_wiki_context_provider_keeps_legacy_signature() -> None:
    from tradecraft.services.market_judgment import _call_wiki_context_provider

    calls: list[dict] = []

    def provider(*, target_scope: str, symbols: list[str]) -> dict:
        calls.append({"target_scope": target_scope, "symbols": symbols})
        return {"status": "ok"}

    payload = _call_wiki_context_provider(
        provider,
        target_scope="kis",
        symbols=["005930"],
        horizons=["market_session:regular"],
    )

    assert payload == {"status": "ok"}
    assert calls == [{"target_scope": "kis", "symbols": ["005930"]}]


def test_market_judgment_run_includes_account_and_position_first(tmp_path: Path) -> None:
    llm = _FakeLLM()
    wiki_calls: list[dict] = []

    def wiki_context_provider(**kwargs) -> dict:
        wiki_calls.append(kwargs)
        return {
            "status": "ok",
            "selection_run_id": "selection:market-test",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "assist",
            "symbols": kwargs.get("symbols", []),
            "content": "Market judge wiki context",
            "pages": [
                {
                    "page_id": "kis:market-judge",
                    "rank": 1,
                    "score": 77.0,
                    "selection_reasons": ["scope_match:kis"],
                    "selection_penalties": [],
                    "char_count": 25,
                    "source_refs": [],
                }
            ],
            "rejected_pages": [],
            "budget_report": {
                "status": "ok",
                "max_chars": 24000,
                "requested_symbol_count": 3,
                "requested_symbol_available_summary_count": 1,
                "requested_symbol_available_summary_symbols": ["005930"],
                "requested_symbol_missing_summary_count": 1,
                "requested_symbol_missing_summary_symbols": ["000660"],
                "requested_symbol_prompt_omitted_count": 1,
                "requested_symbol_prompt_omitted_symbols": ["178920"],
            },
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=lambda **_: {
            "status": "ok",
            "regime": "risk_on",
            "score": 72,
        },
        wiki_context_provider=wiki_context_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        watchlist=["178920"],
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["status"] == "ok"
    assert result["focus_symbols"][0] == "005930"
    assert result["account"]["positions"][0]["symbol"] == "005930"
    assert result["judgments"][0]["account_action"] == "risk_check"
    assert llm.last_payload is not None
    assert llm.last_payload["native_thread_mode"] == "ephemeral"
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert prompt["account"]["cash_krw"] == pytest.approx(1_000_000)
    assert prompt["market_pulse"]["regime"] == "risk_on"
    wiki = prompt["jue_wiki"]
    assert wiki["status"] == "ok"
    assert wiki["selection_run_id"]
    assert wiki["target_scope"] == "kis"
    assert "005930" in wiki["symbols"]
    assert wiki["pages"]
    assert wiki["budget_report"]["status"] == "ok"
    assert "selection_reasons" in wiki["pages"][0]
    assert prompt["jue_wiki_budget_report"]["status"] == "ok"
    assert prompt["jue_wiki_budget_report"]["max_chars"] > 0
    assert (
        prompt["jue_wiki_budget_report"]["total_chars"]
        <= prompt["jue_wiki_budget_report"]["max_chars"]
    )
    assert wiki_calls[0]["target_scope"] == "kis"
    assert "005930" in wiki_calls[0]["symbols"]
    assert wiki_calls[0]["horizons"] == [
        f"market_session:{prompt['clock']['session']}"
    ]
    assert prompt["symbols"][0]["position"]["symbol"] == "005930"
    assert prompt["language_policy"]["internal_reasoning_language"] == "en-US"
    assert prompt["language_policy"]["operator_display_language"] == "ko-KR"
    assert prompt["language_policy"]["user_visible_generation_order"] == (
        "draft_conclusion_in_english_then_translate_to_korean_for_display"
    )
    latest = engine.latest_judgment()
    assert latest["judgments"][0]["symbol"] == "005930"
    assert latest["account"]["position_count"] == 1
    coverage = latest["run"]["source_snapshot"]["jue_wiki_requested_symbol_coverage"]
    assert coverage["missing_summary_symbols"] == ["000660"]
    assert coverage["prompt_omitted_symbols"] == ["178920"]
    assert coverage["required_adjustments"] == [
        {
            "adjustment_type": "coverage_gap_follow_up",
            "reason": "requested_symbols_missing_from_wiki_summary",
            "symbols": ["000660"],
            "resolution": "collect_or_rebuild_summary_before_confident_decision",
        },
        {
            "adjustment_type": "prompt_omission_follow_up",
            "reason": "requested_symbols_omitted_from_prompt_summary",
            "symbols": ["178920"],
            "resolution": "treat_as_reviewed_but_lower_confidence_until_direct_summary_check",
        },
    ]

    quote_only = asyncio.run(engine.run_once(use_llm=False))
    assert quote_only["status"] == "quotes_only"
    assert quote_only["run_id"] == 0
    assert engine.latest_judgment()["run"]["mode"] == "llm"


def test_market_judgment_run_rejects_missing_wiki_validation_repair_resolution(
    tmp_path: Path,
) -> None:
    llm = _MissingValidationRepairResolutionLLM()

    def wiki_context_provider(**kwargs) -> dict:
        return {
            "status": "ok",
            "selection_run_id": "selection:market-validation-contract",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "assist",
            "pages": [{"page_id": "kis.market.regime-test"}],
            "validation_repair_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 5,
                "resolved_count": 0,
                "resolution_rate": 0.0,
                "top_degraded": [
                    {
                        "decision_scope": "market",
                        "discipline_id": "regime_test",
                        "repair_action_id": "refresh_regime_validation",
                        "entry_bias": "wait_for_regime_confirmation",
                        "sample_count": 5,
                        "missed_count": 5,
                        "resolved_count": 0,
                        "resolution_rate": 0.0,
                        "status": "repair_required",
                        "allowed_entry_postures": ["regime_confirmed_wait"],
                        "source_counts": {"market_validation_repair": 5},
                        "legacy_source_counts": {"market_validation_repair": 5},
                        "contract_source_counts": {},
                    }
                ],
            },
            "budget_report": {"selected_count": 1},
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        wiki_context_provider=wiki_context_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["status"] == "error"
    assert result["error_message"] == "validation_repair_resolution_missing_from_model"
    assert result["judgments"] == []
    assert llm.last_payload is not None
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert "jue_wiki_validation_repair_contract" in prompt["decision_inputs"]
    assert "validation_repair_response_contract" in prompt["decision_inputs"]


def test_market_judgment_observe_mode_attaches_wiki_observation_only(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM()

    def wiki_context_provider(**kwargs) -> dict:
        return {
            "status": "ok",
            "selection_run_id": "selection:market-observe",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "observe",
            "symbols": kwargs.get("symbols", []),
            "content": "Market judge wiki context",
            "pages": [
                {
                    "page_id": "kis:market-judge",
                    "rank": 1,
                    "score": 77.0,
                    "selection_reasons": ["scope_match:kis"],
                    "selection_penalties": [],
                    "char_count": 25,
                    "source_refs": [],
                }
            ],
            "rejected_pages": [],
            "budget_report": {"status": "ok", "max_chars": 24000},
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=lambda **_: {"status": "ok"},
        wiki_context_provider=wiki_context_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["status"] == "ok"
    assert llm.last_payload is not None
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert "jue_wiki" not in prompt
    observation = prompt["jue_wiki_selection_observation"]
    assert observation["status"] == "ok"
    assert observation["selection_run_id"]
    assert observation["prompt_mode"] == "observe"
    assert observation["budget_report"]["status"] == "ok"
    assert observation.get("content", "") == ""
    assert "content" not in observation["pages"][0]
    assert "jue_wiki_budget_report" not in prompt


def test_market_judgment_accepts_no_arg_legacy_wiki_provider(tmp_path: Path) -> None:
    llm = _FakeLLM()
    calls = 0

    def wiki_context_provider() -> dict:
        nonlocal calls
        calls += 1
        return {
            "status": "ok",
            "target_scope": "legacy",
            "symbols": ["legacy"],
            "content": "legacy market wiki context",
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=lambda **_: {"status": "ok"},
        wiki_context_provider=wiki_context_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["status"] == "ok"
    assert calls == 1
    assert llm.last_payload is not None
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert prompt["jue_wiki"]["status"] == "ok"
    assert prompt["jue_wiki"]["content"] == "legacy market wiki context"


def test_market_judgment_primary_mode_marks_wiki_as_evidence_policy(
    tmp_path: Path,
) -> None:
    llm = _FakeLLM()

    def wiki_context_provider(**kwargs) -> dict:
        return {
            "status": "ok",
            "selection_run_id": "selection:market-primary",
            "target_scope": kwargs.get("target_scope"),
            "prompt_mode": "primary",
            "symbols": kwargs.get("symbols", []),
            "content": "Market primary wiki context",
            "pages": [
                {
                    "page_id": "kis:market-judge",
                    "rank": 1,
                    "score": 77.0,
                    "selection_reasons": ["scope_match:kis"],
                    "selection_penalties": [],
                    "char_count": 27,
                    "source_refs": [],
                }
            ],
            "rejected_pages": [],
            "budget_report": {"status": "ok", "max_chars": 24000},
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=lambda **_: {"status": "ok"},
        wiki_context_provider=wiki_context_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["status"] == "ok"
    assert llm.last_payload is not None
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert prompt["jue_wiki"]["primary_context"] is True
    assert prompt["jue_wiki"]["raw_context_policy"] == "evidence_only"
    assert prompt["jue_wiki_primary_context_policy"]["raw_context_policy"] == (
        "evidence_only"
    )


def test_market_judgment_suppresses_legacy_research_runner_gap_when_sources_are_active(
    tmp_path: Path,
) -> None:
    llm = _ResearchRunnerGapLLM()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=lambda **_: {
            "status": "ok",
            "regime": "risk_on",
            "score": 72,
        },
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["judgments"][0]["data_gaps"] == ["고래"]
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert prompt["research"]["status"] == "optional_disabled"


def test_market_judgment_merges_opportunity_candidates_after_priority_symbols(
    tmp_path: Path,
) -> None:
    kis = _RecordingKIS()

    def opportunity_provider(**kwargs) -> dict:
        assert kwargs["limit"] == 4
        return {
            "status": "ok",
            "pool_count": 320,
            "generated_at": "2026-05-22T00:00:00+00:00",
            "coverage": {"symbol_count": 300, "position_count": 1},
            "candidates": [
                {
                    "symbol": "123456",
                    "name": "스캐너후보",
                    "score": 91,
                    "opportunity_score": 91,
                    "sources": ["reports", "fundamentals"],
                    "reasons": ["broad scanner"],
                },
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "score": 80,
                    "sources": ["account_position"],
                },
            ],
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=4,
            llm_max_symbols=3,
            use_naver_fallback=False,
        ),
        kis=kis,  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        watchlist=["178920"],
        opportunity_provider=opportunity_provider,
    )

    result = asyncio.run(engine.run_once(use_llm=False))

    assert kis.quoted_symbols == ["005930", "178920", "000660", "123456"]
    assert result["focus_symbols"] == ["005930", "000660", "123456"]
    assert result["candidate_coverage"]["pool_count"] == 320
    assert engine.status()["candidate_coverage"]["last_scan_at"] == (
        "2026-05-22T00:00:00+00:00"
    )
    engine._last_opportunity_scan = {"status": "not_scanned"}  # noqa: SLF001
    assert engine.latest_judgment()["candidate_coverage"]["pool_count"] == 320


def test_market_judgment_prefers_resolved_symbol_names_over_stale_candidate_names(
    tmp_path: Path,
) -> None:
    kis = _RecordingKIS()

    def opportunity_provider(**kwargs) -> dict:
        _ = kwargs
        return {
            "status": "ok",
            "pool_count": 1,
            "candidates": [
                {
                    "symbol": "005930",
                    "name": "코리안리",
                    "score": 91,
                    "sources": ["reports"],
                },
            ],
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=2,
            llm_max_symbols=2,
            use_naver_fallback=False,
        ),
        kis=kis,  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        report_repository=_FakeReportRepository({"005930": "삼성전자"}),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["focus_symbols"][0] == "005930"
    assert result["judgments"][0]["name"] == "삼성전자"


def test_market_judgment_ignores_generic_resolved_symbol_names(
    tmp_path: Path,
) -> None:
    kis = _RecordingKIS()

    def opportunity_provider(**kwargs) -> dict:
        _ = kwargs
        return {
            "status": "ok",
            "pool_count": 1,
            "candidates": [
                {
                    "symbol": "010280",
                    "name": "아이티센엔텍",
                    "score": 91,
                    "sources": ["reports"],
                },
            ],
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=10,
            use_naver_fallback=False,
        ),
        kis=kis,  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        report_repository=_FakeReportRepository({"010280": "Revi"}),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        watchlist=["010280"],
    )

    context = engine._context_maps(  # noqa: SLF001
        account={"positions": []},
        strategy_payload={
            "candidates": [
                {
                    "symbol": "010280",
                    "name": "아이티센엔텍",
                    "score": 91,
                }
            ]
        },
        quotes=[],
    )

    assert context["names"]["010280"] == "아이티센엔텍"


def test_market_judgment_accepts_market_pulse_service_latest_provider(
    tmp_path: Path,
) -> None:
    pulse = MarketPulseService(
        config=MarketPulseConfig(db_path=str(tmp_path / "market_pulse.db")),
    )
    pulse.repository.save_snapshot(
        {
            "status": "ok",
            "captured_at": "2026-05-10T00:00:00+00:00",
            "trading_day": "2026-05-10",
            "regime": "rotation",
            "score": 62.5,
            "score_method_version": "v3",
            "score_components": {"total_score": 62.5},
            "indices": [],
            "investor_flows": [],
            "program_trading": [],
            "futures": {"status": "missing"},
            "fx": {"status": "missing"},
            "sectors": {"status": "missing", "items": []},
            "block_alignment": [],
            "block_exposure": {"status": "ok", "block_count": 0},
            "risk_flags": [],
            "data_gaps": [],
        }
    )
    llm = _FakeLLM()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=pulse.latest,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))

    assert result["market_pulse"]["status"] == "ok"
    assert result["market_pulse"]["score_method_version"] == "v3"
    assert llm.last_payload is not None
    prompt = json.loads(llm.last_payload["messages"][1]["content"])
    assert prompt["market_pulse"]["score"] == pytest.approx(62.5)


def test_collect_account_returns_error_without_stale_fallback_on_fetch_error(
    tmp_path: Path,
) -> None:
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    ok_account = asyncio.run(engine.collect_account())
    engine.kis = _FailingKIS()  # type: ignore[assignment]
    error_account = asyncio.run(engine.collect_account())

    assert ok_account["status"] == "ok"
    assert error_account["status"] == "error"
    assert error_account["positions"] == []
    assert error_account["position_count"] == 0
    assert error_account["cash_krw"] == pytest.approx(0.0)
    assert "token rate limited" in error_account["error_message"]


def test_market_judgment_llm_failure_does_not_emit_deterministic_judgments(
    tmp_path: Path,
) -> None:
    llm = _FailingLLM("native runtime timeout")
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))
    latest = engine.latest_judgment()

    assert result["status"] == "error"
    assert result["judgments"] == []
    assert "native runtime timeout" in result["error_message"]
    assert latest["run"]["status"] == "error"
    assert latest["run"]["mode"] == "error"
    assert latest["judgments"] == []


def test_market_judgment_quote_only_skips_memory_context_provider(
    tmp_path: Path,
) -> None:
    calls = 0

    def memory_context_provider(symbols, quotes, account, strategy, market_pulse):
        nonlocal calls
        _ = (symbols, quotes, account, strategy, market_pulse)
        calls += 1
        return {"status": "ok"}

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        memory_context_provider=memory_context_provider,
    )

    result = asyncio.run(engine.run_once(use_llm=False))

    assert result["status"] == "quotes_only"
    assert result["judgments"] == []
    assert calls == 0


def test_market_judgment_quote_only_reuses_cached_strategy_scan(
    tmp_path: Path,
) -> None:
    class _CountingStrategy(_FakeStrategy):
        def __init__(self) -> None:
            self.build_calls = 0
            self.external_calls = 0

        def build_candidates(self, *, query, research_feed, limit=None) -> dict:
            self.build_calls += 1
            return super().build_candidates(
                query=query,
                research_feed=research_feed,
                limit=limit,
            )

        def list_external_signals(self, **kwargs) -> dict:
            self.external_calls += 1
            return super().list_external_signals(**kwargs)

    opportunity_calls = 0

    def opportunity_provider(*, limit, account=None, strategy_payload=None) -> dict:
        nonlocal opportunity_calls
        _ = (limit, account, strategy_payload)
        opportunity_calls += 1
        return {
            "status": "ok",
            "candidates": [{"symbol": "178920", "name": "PI첨단소재"}],
            "pool_count": 1,
        }

    strategy = _CountingStrategy()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=_FakeLLM(),  # type: ignore[arg-type]
        strategy_engine=strategy,  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        opportunity_provider=opportunity_provider,
    )

    first = asyncio.run(engine.run_once(use_llm=True))
    quote_only = asyncio.run(engine.run_once(use_llm=False))

    assert first["status"] == "ok"
    assert quote_only["status"] == "quotes_only"
    assert strategy.build_calls == 1
    assert strategy.external_calls == 1
    assert opportunity_calls == 1
    assert "178920" in quote_only["focus_symbols"]


def test_market_judgment_prompt_includes_jue_decision_skills(
    tmp_path: Path,
    memory_context_provider,
) -> None:
    llm = _FakeLLM()
    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        memory_context_provider=memory_context_provider,
    )

    result = asyncio.run(engine.run_once(use_llm=True))
    prompt = json.loads(llm.last_payload["messages"][1]["content"])

    assert result["status"] == "ok"
    assert prompt["required_decision_skills"] == ["market_judge", "risk_manager"]
    assert prompt["investment_memory"]["persona"].startswith("쥬는")
    assert prompt["investment_memory"]["received_market_pulse_status"] == "missing"
    assert prompt["investment_memory"]["decision_skills"]["market_judge"]["version"] == (
        "jue.market_judge.v1"
    )


def test_market_judgment_accepts_investment_memory_context_pack_provider(
    tmp_path: Path,
) -> None:
    strategy_path = tmp_path / "strategy_krx.md"
    strategy_path.write_text("# 전략\n\n- 장중 판단은 리스크를 먼저 본다.", encoding="utf-8")
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=str(tmp_path / "memory"),
            db_path=str(tmp_path / "memory.db"),
            strategy_md_path=str(strategy_path),
        )
    )
    memory.initialize()
    llm = _FakeLLM()

    def market_pulse_provider(*, symbols, quotes, strategy):
        assert symbols
        assert quotes
        assert strategy
        return {
            "status": "ok",
            "regime": "rotation",
            "score": 62.5,
            "score_method_version": "v3",
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=market_pulse_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
        memory_context_provider=memory.context_pack,
    )

    result = asyncio.run(engine.run_once(use_llm=True))
    prompt = json.loads(llm.last_payload["messages"][1]["content"])

    assert result["status"] == "ok"
    assert prompt["investment_memory"]["status"] == "ok"
    assert prompt["investment_memory"]["market_pulse"]["regime"] == "rotation"
    assert prompt["investment_memory"]["market_pulse"]["score"] == pytest.approx(62.5)
    assert prompt["investment_memory"]["market_pulse"]["score_method_version"] == "v3"
    assert prompt["investment_memory"]["decision_skills"]["market_judge"]["version"] == (
        "jue.market_judge.v1"
    )


def test_market_judgment_prompt_compacts_large_operating_contexts(
    tmp_path: Path,
) -> None:
    marker = "RAW_CONTEXT_MARKER_SHOULD_NOT_REACH_PROMPT"
    llm = _FakeLLM()

    def market_pulse_provider(*, symbols, quotes, strategy):
        _ = (symbols, quotes, strategy)
        return {
            "status": "ok",
            "regime": "rotation",
            "score": 78,
            "indices": [{"code": "KOSPI", "change_pct": 1.2, "raw": marker * 300}],
            "sectors": {
                "status": "ok",
                "items": [
                    {"name": f"sector-{idx}", "score": idx, "raw": marker * 100}
                    for idx in range(20)
                ],
            },
            "investor_flows": [
                {"market": "KOSPI", "foreign_net": 1000, "raw": marker * 120}
            ],
            "program_trading": [{"market": "KOSPI", "net": 2000, "raw": marker * 120}],
            "raw": marker * 500,
        }

    def memory_context_provider(symbols, quotes, account, strategy, market_pulse):
        _ = (symbols, quotes, account, strategy, market_pulse)
        return {
            "status": "ok",
            "persona": "쥬는 공격적으로 기회를 찾되 안전 게이트를 존중한다. " + marker * 120,
            "policy_rules": [
                {
                    "policy_id": f"policy-{idx}",
                    "status": "active_caution",
                    "reason": marker * 80,
                }
                for idx in range(30)
            ],
            "symbol_analyses": {
                "005930": [
                    {
                        "symbol": "005930",
                        "summary": "삼성전자 중기 메모리",
                        "raw": marker * 100,
                    }
                ]
            },
            "decision_skills": {
                "market_judge": {
                    "version": "jue.market_judge.v1",
                    "content_md": "# skill\n" + marker * 300,
                }
            },
            "raw": marker * 1000,
        }

    engine = MarketJudgmentEngine(
        config=MarketJudgmentConfig(
            db_path=str(tmp_path / "market_judgment.db"),
            max_symbols=10,
            llm_max_symbols=5,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        codex_runtime=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        market_pulse_provider=market_pulse_provider,
        memory_context_provider=memory_context_provider,
        calendar=_OpenCalendar(),  # type: ignore[arg-type]
    )

    result = asyncio.run(engine.run_once(use_llm=True))
    assert result["status"] == "ok"
    assert llm.last_payload is not None
    prompt_text = llm.last_payload["messages"][1]["content"]
    prompt = json.loads(prompt_text)

    assert marker not in prompt_text
    assert len(prompt_text) < 25_000
    assert prompt["account"]["orderable_cash_krw"] == pytest.approx(1_000_000)
    assert prompt["market_pulse"]["regime"] == "rotation"
    assert prompt["investment_memory"]["symbol_analyses"]["005930"][0]["summary"] == (
        "삼성전자 중기 메모리"
    )
    assert prompt["prompt_budget"]["compacted"] is True
    assert prompt["aggressive_trader_policy"]["style"] == "aggressive_genius_trader"
    assert "비대칭" in " ".join(prompt["aggressive_trader_policy"]["principles"])
def test_market_judgment_jue_wiki_prompt_context_attaches_application_metadata() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market",
            "prompt_mode": "assist",
            "configured_prompt_mode": "assist",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:market-assist",
                "recommended_mode": "assist",
                "sample_count": 28,
                "confidence": 0.65,
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_assist_recommendation",
            },
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "probe",
                        "sample_count": 4,
                    }
                ],
            },
            "pages": [{"page_id": "kis.regime.krx_opening"}],
            "requested_symbol_summaries": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbol": "005930",
                    "summary": "삼성전자 압축 종목 기억",
                    "memory_card": {"stance": "중기 저점 대기"},
                }
            ],
            "budget_report": {"char_count": 10},
        },
        max_chars=1000,
    )

    assert prompt["jue_wiki_application"] | {"budget_report": {"char_count": 10}} == {
        "status": "ok",
        "selection_run_id": "selection:market",
        "prompt_mode": "assist",
        "configured_prompt_mode": "assist",
        "mode_recommendation": {
            "recommendation_id": "wiki-mode:market-assist",
            "recommended_mode": "assist",
            "sample_count": 28,
            "confidence": 0.65,
        },
        "prompt_mode_policy": {
            "source": "mode_recommendation",
            "reason": "validated_assist_recommendation",
        },
        "trust_profile": {
            "prompt_mode": "assist",
            "authority": "supporting_evidence",
            "trust_level": "medium",
            "decision_use": (
                "use selected wiki pages as supporting evidence alongside live "
                "quotes, account state, research, and risk gates"
            ),
            "posture": "validated_mode_recommendation",
            "configured_prompt_mode": "assist",
            "recommended_mode": "assist",
            "recommendation_id": "wiki-mode:market-assist",
            "sample_count": 28,
            "confidence": 0.65,
            "policy_reason": "validated_assist_recommendation",
            "authority_effectiveness": {
                "status": "probe",
                "sample_count": 4,
            },
            "usage_contract": {
                "version": "jue_wiki_usage_contract_v1",
                "decision_role": "supporting_evidence",
                "effectiveness_status": "probe",
                "risk_posture": "supporting_evidence",
                "standalone_trade_authority": False,
                "requires_live_cross_check": True,
                "hard_blocker": False,
                "allowed_uses": [
                    "candidate_ranking",
                    "target_stop_context",
                    "risk_note_context",
                    "follow_up_research",
                ],
                "required_cross_checks": [
                    "live_quote",
                    "account_state",
                    "risk_gate",
                    "fresh_research_conflicts",
                    "current_price_structure",
                ],
                "conflict_resolution": (
                    "prefer_live_execution_data_and_record_wiki_repair"
                ),
            },
        },
        "trust_profile_effectiveness": {
            "target_scope": "kis",
            "trust_profile_count": 1,
            "trust_profiles": [
                {
                    "authority": "supporting_evidence",
                    "status": "probe",
                    "sample_count": 4,
                }
            ],
        },
        "selected_page_ids": ["kis.regime.krx_opening"],
        "requested_symbol_summary_page_ids": ["kis.symbol.005930"],
        "applied_page_ids": ["kis.regime.krx_opening", "kis.symbol.005930"],
        "requested_symbol_summary_count": 1,
        "budget_report": {"char_count": 10},
    }
    assert prompt["jue_wiki_application"]["budget_report"]["char_count"] == 10
    assert "prompt_payload_chars" in prompt["jue_wiki_application"]["budget_report"]


def test_market_judgment_jue_wiki_requested_symbol_coverage_distinguishes_missing_and_omitted() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {
        "output_schema": {
            "judgments": [{"symbol": "6-digit KRX code"}],
        }
    }

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-missing-vs-omitted",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [{"page_id": "kis.symbol.005930"}],
            "requested_symbol_summaries": [
                {"page_id": "kis.symbol.005930", "symbol": "005930"}
            ],
            "budget_report": {
                "requested_symbol_count": 4,
                "requested_symbol_summary_count": 2,
                "requested_symbol_summary_coverage_status": "partial",
                "requested_symbol_unsummarized_count": 2,
                "requested_symbol_unsummarized_symbols": ["000660", "277810"],
                "requested_symbol_missing_summary_count": 1,
                "requested_symbol_missing_summary_symbols": ["000660"],
                "requested_symbol_prompt_omitted_count": 1,
                "requested_symbol_prompt_omitted_symbols": ["277810"],
            },
        },
        max_chars=1000,
    )

    plan = prompt["jue_wiki_application"]["requested_symbol_coverage_action_plan"]
    assert plan["missing_summary_symbols"] == ["000660"]
    assert plan["prompt_omitted_symbols"] == ["277810"]
    assert plan["required_adjustments"] == [
        {
            "adjustment_type": "coverage_gap_follow_up",
            "reason": "requested_symbols_missing_from_wiki_summary",
            "symbols": ["000660"],
            "resolution": "collect_or_rebuild_summary_before_confident_decision",
        },
        {
            "adjustment_type": "prompt_omission_follow_up",
            "reason": "requested_symbols_omitted_from_prompt_summary",
            "symbols": ["277810"],
            "resolution": "treat_as_reviewed_but_lower_confidence_until_direct_summary_check",
        },
    ]
    assert "jue_wiki_requested_symbol_coverage" in prompt["decision_inputs"]
    contract = prompt["jue_wiki_requested_symbol_coverage"]
    assert contract["missing_summary_symbols"] == ["000660"]
    assert contract["prompt_omitted_symbols"] == ["277810"]
    assert contract["required_adjustments"] == plan["required_adjustments"]


def test_market_judgment_jue_wiki_requested_symbol_coverage_flags_degraded_summaries() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-degraded-summary",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [{"page_id": "kis.symbol.005930"}],
            "requested_symbol_summaries": [
                {
                    "page_id": "kis.symbol.005930",
                    "symbol": "005930",
                    "freshness": "stale",
                    "quality_status": "degraded",
                    "quality_warnings": ["valuation_stale_gt_30d"],
                }
            ],
            "budget_report": {
                "requested_symbol_count": 1,
                "requested_symbol_summary_count": 1,
                "requested_symbol_summary_coverage_status": "full",
                "requested_symbol_unsummarized_count": 0,
                "requested_symbol_unsummarized_symbols": [],
                "requested_symbol_degraded_summary_count": 1,
                "requested_symbol_degraded_summary_symbols": ["005930"],
                "requested_symbol_degraded_summary_reasons": [
                    {
                        "symbol": "005930",
                        "freshness": "stale",
                        "quality_status": "degraded",
                        "quality_warnings": ["valuation_stale_gt_30d"],
                    }
                ],
            },
        },
        max_chars=1000,
    )

    plan = prompt["jue_wiki_application"]["requested_symbol_coverage_action_plan"]
    assert plan["degraded_summary_count"] == 1
    assert plan["degraded_summary_symbols"] == ["005930"]
    assert plan["required_adjustments"] == [
        {
            "adjustment_type": "degraded_summary_cross_check",
            "reason": "requested_symbol_summary_stale_or_weak",
            "symbols": ["005930"],
            "resolution": "cross_check_live_research_and_lower_confidence_until_refreshed",
        }
    ]
    contract = prompt["jue_wiki_requested_symbol_coverage"]
    assert contract["status"] == "full"
    assert contract["degraded_summary_symbols"] == ["005930"]
    assert contract["degraded_summary_reasons"] == [
        {
            "symbol": "005930",
            "freshness": "stale",
            "quality_status": "weak",
            "quality_warnings": ["valuation_stale_gt_30d"],
        }
    ]


def test_market_judgment_jue_wiki_observe_prompt_context_preserves_mode_policy() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {
        "output_schema": {
            "judgments": [{"symbol": "6-digit KRX code"}],
        }
    }

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-observe-protective",
            "prompt_mode": "observe",
            "configured_prompt_mode": "primary",
            "mode_recommendation": {
                "recommendation_id": "wiki-mode:market-observe",
                "recommended_mode": "observe",
                "sample_count": 42,
                "confidence": 0.76,
                "reasons": [
                    "prompt_mode_effectiveness:primary:degraded",
                    "primary_avg_return_pct:-0.6200",
                ],
            },
            "prompt_mode_policy": {
                "source": "mode_recommendation",
                "reason": "validated_observe_recommendation",
            },
            "pages": [{"page_id": "kis.regime.degraded_primary"}],
            "repair_priority_effectiveness": {
                "status": "repair_required",
                "sample_count": 7,
                "missed_count": 5,
                "resolved_count": 2,
                "resolution_rate": 2 / 7,
                "raw_debug": "DROP_ME",
                "repair_loop_status_metrics": [
                    {
                        "decision_scope": "market",
                        "repair_loop_status": "repair_required",
                        "action_type": "refresh_market_regime_context",
                        "sample_count": 7,
                        "missed_count": 5,
                        "resolved_count": 2,
                        "resolution_rate": 2 / 7,
                        "status": "repair_required",
                    }
                ],
            },
            "validation_repair_effectiveness": {
                "status": "repair_required",
                "sample_count": 5,
                "missed_count": 4,
                "resolved_count": 1,
                "resolution_rate": 0.2,
                "raw_debug": "DROP_ME",
                "top_degraded": [
                    {
                        "decision_scope": "market",
                        "discipline_id": "regime_test",
                        "repair_action_id": "refresh_regime_validation",
                        "entry_bias": "wait_for_regime_confirmation",
                        "sample_count": 5,
                        "missed_count": 4,
                        "resolved_count": 1,
                        "resolution_rate": 0.2,
                        "status": "repair_required",
                        "allowed_entry_postures": ["regime_confirmed_wait"],
                        "sources": ["market_validation_repair"],
                        "source_counts": {"market_validation_repair": 5},
                    }
                ],
            },
            "wiki_application_coverage": {
                "status": "warning",
                "decision_scope": "market",
                "raw_debug": "DROP_ME",
                "coverage": {
                    "decision_scope": "market",
                    "decision_link_count": 4,
                    "decision_links_with_selected_wiki_pages": 2,
                    "decision_links_with_selected_wiki_pages_pct": 50.0,
                    "selection_outcome_count": 2,
                    "selection_outcomes_with_selected_wiki_page": 1,
                    "selection_outcomes_with_selected_wiki_page_pct": 50.0,
                },
                "alerts": [
                    {
                        "severity": "warning",
                        "code": "wiki_outcome_feedback_missing",
                        "decision_scope": "market",
                        "message": "DROP_ME_LONG_MESSAGE",
                        "action": "project_selection_outcomes_and_page_effectiveness",
                    }
                ],
            },
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "jue_wiki" not in prompt
    application = prompt["jue_wiki_application"]
    assert application["configured_prompt_mode"] == "primary"
    assert application["mode_recommendation"]["recommendation_id"] == (
        "wiki-mode:market-observe"
    )
    assert application["prompt_mode_policy"]["reason"] == (
        "validated_observe_recommendation"
    )
    assert application["trust_profile"]["authority"] == "observation_only"
    assert application["trust_profile"]["posture"] == (
        "primary_demoted_after_underperformance"
    )
    observation_text = json.dumps(
        prompt["jue_wiki_selection_observation"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "DROP_ME" not in observation_text
    assert prompt["jue_wiki_selection_observation"]["repair_priority_effectiveness"][
        "repair_loop_status_summary"
    ]["primary_repair_action_type"] == "refresh_market_regime_context"
    validation = prompt["jue_wiki_selection_observation"][
        "validation_repair_effectiveness"
    ]
    assert validation["status"] == "repair_required"
    assert validation["top_degraded"][0]["discipline_id"] == "regime_test"
    assert "raw_debug" not in validation
    assert prompt["jue_wiki_application"]["validation_repair_effectiveness"][
        "top_degraded"
    ][0]["repair_action_id"] == "refresh_regime_validation"
    assert prompt["jue_wiki_validation_repair_effectiveness"]["top_degraded"][0][
        "entry_bias"
    ] == "wait_for_regime_confirmation"
    validation_plan = prompt["jue_wiki_validation_repair_effectiveness"][
        "validation_repair_action_plan"
    ]
    assert validation_plan["top_disciplines"] == ["regime_test"]
    assert validation_plan["allowed_entry_postures"] == ["regime_confirmed_wait"]
    assert validation_plan["requires_validation_repair_resolution"] is True
    assert "jue_wiki_validation_repair_effectiveness" in prompt["decision_inputs"]
    validation_contract = prompt["jue_wiki_validation_repair_contract"]
    assert validation_contract["version"] == "jue_wiki_validation_repair_contract_v1"
    assert validation_contract["status"] == "repair_required"
    assert validation_contract["hard_blocker"] is False
    assert validation_contract["requires_validation_repair_resolution"] is True
    assert validation_contract["top_disciplines"] == ["regime_test"]
    assert validation_contract["repair_action_ids"] == ["refresh_regime_validation"]
    assert validation_contract["contract_feedback_gap"]["legacy_sample_count"] == 5
    assert validation_contract["accepted_resolutions"] == [
        "smaller_probe_block",
        "waiting_entry_with_validation_repair_resolution",
        "candidate_reject_with_missing_validation_named",
        "regime_confirmed_wait",
        "risk_check_defer",
        "new_watch_with_trigger",
        "no_new_entry_until_required_validation_repair_is_resolved",
    ]
    assert "jue_wiki_validation_repair_contract" in prompt["decision_inputs"]
    assert prompt["jue_wiki_contract_feedback_gap"] == {
        "status": "missing_contract_outcomes",
        "legacy_sample_count": 5,
        "contract_sample_count": 0,
        "required_response": (
            "record validation_repair_resolution and resolved_candidates so "
            "future wiki updates can measure contract effectiveness"
        ),
        "source_contract": "jue_wiki_validation_repair_contract",
    }
    assert "jue_wiki_contract_feedback_gap" in prompt["decision_inputs"]
    assert "validation_repair_response_contract" in prompt["decision_inputs"]
    response_contract = prompt["validation_repair_response_contract"]
    assert response_contract["version"] == "market_validation_repair_response_contract_v1"
    assert response_contract["required_when"] == (
        "jue_wiki_validation_repair_contract requires validation repair "
        "resolution or jue_wiki_contract_feedback_gap is present"
    )
    assert response_contract["required_output"] == "validation_repair_resolution"
    resolution_schema = prompt["output_schema"]["validation_repair_resolution"]
    assert resolution_schema["required"].startswith(
        "mandatory when jue_wiki_contract_feedback_gap is present"
    )
    assert resolution_schema["resolved_candidates"][0]["resolution"] == (
        "regime_confirmed_wait|candidate_rejected|risk_check_defer|"
        "new_watch_with_trigger"
    )
    assert prompt["jue_wiki_selection_observation"]["wiki_application_coverage"][
        "coverage"
    ]["decision_link_count"] == 4
    assert "raw_debug" not in prompt["jue_wiki_selection_observation"][
        "wiki_application_coverage"
    ]
    assert prompt["jue_wiki_application"]["wiki_application_coverage"][
        "alerts"
    ][0]["code"] == "wiki_outcome_feedback_missing"
    assert prompt["jue_wiki_application_coverage"]["coverage"][
        "selection_outcomes_with_selected_wiki_page_pct"
    ] == 50.0
    assert "jue_wiki_application_coverage" in prompt["decision_inputs"]
    assert prompt["jue_wiki_repair_contract"]["repair_loop_effectiveness"][
        "repair_loop_status_summary"
    ]["repair_action_targets"] == [
        {
            "decision_scope": "market",
            "status": "repair_required",
            "action_type": "refresh_market_regime_context",
            "sample_count": 7,
            "missed_count": 5,
            "resolved_count": 2,
            "metric_count": 1,
            "resolution_rate": 2 / 7,
            "miss_rate": round(5 / 7, 6),
            "repair_pressure_score": round(5 * (5 / 7), 6),
            "recommended_resolution": "refresh_market_context_then_use_waiting_block",
            "resolution_steps": [
                "refresh_regime_and_flow_context",
                "prefer_waiting_block_until_context_confirms",
                "record_regime_repair_outcome",
            ],
            "resolution_success_criteria": [
                "market_regime_context_refreshed",
                "waiting_block_used_when_unconfirmed",
                "regime_repair_outcome_recorded",
            ],
        }
    ]


def test_market_judgment_jue_wiki_observe_context_keeps_requested_symbol_coverage() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-observe-coverage",
            "prompt_mode": "observe",
            "target_scope": "kis",
            "pages": [{"page_id": "kis.regime.observe"}],
            "budget_report": {
                "requested_symbol_count": 2,
                "requested_symbol_summary_count": 1,
                "requested_symbol_summary_coverage_status": "partial",
                "requested_symbol_unsummarized_count": 1,
                "requested_symbol_unsummarized_symbols": ["000660"],
                "requested_symbol_missing_summary_count": 1,
                "requested_symbol_missing_summary_symbols": ["000660"],
                "requested_symbol_prompt_omitted_count": 0,
                "requested_symbol_prompt_omitted_symbols": [],
            },
        },
        max_chars=1000,
    )

    assert "jue_wiki" not in prompt
    assert "jue_wiki_requested_symbol_coverage" in prompt["decision_inputs"]
    assert prompt["jue_wiki_requested_symbol_coverage"]["missing_summary_symbols"] == [
        "000660"
    ]


def test_market_judgment_jue_wiki_application_exposes_decision_adjustments() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 14,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "status": "degraded",
                                "sample_count": 7,
                                "avg_return_pct": -0.28,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "status": "active",
                                "sample_count": 5,
                                "avg_return_pct": 0.22,
                            },
                        ],
                    }
                ],
            },
            "pages": [{"page_id": "kis.regime.supporting"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert prompt["jue_wiki_application"]["decision_adjustments"] == [
        {
            "source": "usage_contract.risk_posture_guidance",
            "action": "shift_to_preferred_risk_posture",
            "target_risk_posture": "repair_probe",
            "reason": "current_risk_posture_degraded",
            "current_risk_posture": "supporting_evidence",
            "current_status": "degraded",
            "recommended_allowed_uses": [
                "repair_candidate_design",
                "small_probe_block",
                "waiting_block",
                "candidate_level_reject",
            ],
            "deprioritized_allowed_uses": [
                "candidate_ranking",
                "target_stop_context",
                "risk_note_context",
                "follow_up_research",
            ],
        }
    ]
    assert "jue_wiki_decision_adjustments" in prompt["decision_inputs"]


def test_market_judgment_jue_wiki_prompt_context_removes_stale_decision_adjustments_input() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {
        "decision_inputs": ["account", "jue_wiki_decision_adjustments"],
    }

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-no-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 14,
                    }
                ],
            },
            "pages": [{"page_id": "kis.regime.clean"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "decision_adjustments" not in prompt["jue_wiki_application"]
    assert prompt["decision_inputs"] == ["account"]


def test_market_judgment_jue_wiki_decision_adjustment_audit_contract_attaches_and_clears() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-audit-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 20,
                        "risk_posture_metrics": [
                            {
                                "risk_posture": "supporting_evidence",
                                "status": "degraded",
                                "sample_count": 9,
                                "avg_return_pct": -0.45,
                                "confidence": 1.0,
                            },
                            {
                                "risk_posture": "repair_probe",
                                "status": "active",
                                "sample_count": 6,
                                "avg_return_pct": 0.64,
                                "confidence": 1.0,
                            },
                        ],
                        "decision_adjustment_metrics": [
                            {
                                "action": "shift_to_preferred_risk_posture",
                                "target_risk_posture": "repair_probe",
                                "reason": "current_risk_posture_degraded",
                                "sample_count": 5,
                                "status": "degraded",
                                "avg_return_pct": -0.7,
                                "confidence": 1.0,
                            }
                        ],
                    }
                ],
            },
            "pages": [{"page_id": "kis.regime.audit"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    application = prompt["jue_wiki_application"]
    assert application["decision_adjustments"][0]["action"] == (
        "audit_preferred_risk_posture_before_shift"
    )
    assert prompt["jue_wiki_decision_adjustment_audit_contract"] == {
        "version": "jue_wiki_decision_adjustment_audit_contract_v1",
        "status": "active",
        "adjustment_count": 1,
        "actions": ["audit_preferred_risk_posture_before_shift"],
        "target_risk_postures": ["repair_probe"],
        "required_review": [
            "verify why prior shift_to_preferred_risk_posture underperformed",
            "compare live quote, account state, risk gate, and fresh evidence before adopting target risk posture",
            "if evidence remains weak, use repair probe, waiting block, or explicit rejection instead of direct escalation",
        ],
        "accepted_resolutions": [
            "adopt target risk posture with explicit live evidence override",
            "create a smaller repair probe or waiting block",
            "keep current posture and record what evidence is missing",
            "reject the shift and create a wiki repair note",
        ],
        "hard_blocker": False,
        "safety_gates_still_override": True,
    }
    assert "jue_wiki_decision_adjustment_audit_contract" in prompt["decision_inputs"]

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-no-audit-adjust",
            "prompt_mode": "assist",
            "trust_profile_effectiveness": {
                "target_scope": "kis",
                "trust_profile_count": 1,
                "trust_profiles": [
                    {
                        "authority": "supporting_evidence",
                        "status": "active",
                        "sample_count": 20,
                    }
                ],
            },
            "pages": [{"page_id": "kis.regime.clean"}],
            "budget_report": {"selected_count": 1},
        },
        max_chars=1000,
    )

    assert "decision_adjustments" not in prompt["jue_wiki_application"]
    assert "jue_wiki_decision_adjustment_audit_contract" not in prompt
    assert "jue_wiki_decision_adjustment_audit_contract" not in prompt.get(
        "decision_inputs", []
    )


def test_market_judgment_jue_wiki_prompt_context_compacts_large_payload() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    marker = "MARKET_JUDGE_WIKI_RAW_SHOULD_BE_TRIMMED"
    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"} for _ in range(8)]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-large",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "content": marker * 1000,
            "pages": [
                {
                    "page_id": f"page-{idx}",
                    "rank": idx,
                    "score": 80 - idx,
                    "content": marker * 200,
                    "selection_reasons": [marker * 10],
                    "source_refs": [f"ref-{idx}"],
                }
                for idx in range(20)
            ],
            "rejected_pages": [
                {
                    "page_id": f"rejected-{idx}",
                    "content": marker * 200,
                    "selection_penalties": [marker * 10],
                }
                for idx in range(30)
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": f"{idx:06d}",
                    "page_id": f"kis.symbol.{idx:06d}",
                    "summary": "KEEP_SUMMARY. " + (marker * 120),
                    "memory_card": {
                        "stance": "KEEP_STANCE. " + (marker * 120),
                        "trading_history": marker * 200,
                        "lessons": marker * 200,
                        "open_questions": marker * 200,
                    },
                }
                for idx in range(20)
            ],
            "budget_report": {"status": "raw"},
        },
        max_chars=12_000,
    )
    prompt_text = json.dumps(prompt, ensure_ascii=False, sort_keys=True)

    assert len(json.dumps(prompt["jue_wiki"], ensure_ascii=False)) <= 12_000
    assert len(prompt_text) <= 15_000
    assert prompt["jue_wiki"]["budget_report"]["prompt_payload_status"] == "compacted"
    assert len(prompt["jue_wiki"]["pages"]) <= 12
    assert len(prompt["jue_wiki"]["rejected_pages"]) <= 20
    assert len(prompt["jue_wiki"]["requested_symbol_summaries"]) <= 8
    assert "KEEP_SUMMARY" in prompt["jue_wiki"]["requested_symbol_summaries"][0][
        "summary"
    ]
    assert marker not in json.dumps(
        prompt["jue_wiki"]["requested_symbol_summaries"],
        ensure_ascii=False,
    )
    assert "MARKET_JUDGE_WIKI_RAW_SHOULD_BE_TRIMMED" not in json.dumps(
        prompt["jue_wiki"]["pages"],
        ensure_ascii=False,
    )


def test_market_judgment_jue_wiki_prompt_context_keeps_page_freshness_quality_metadata() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-page-quality",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 87.5,
                    "freshness": "stale",
                    "quality_status": "weak",
                    "quality_warnings": [
                        "valuation_stale_gt_30d",
                        "price_missing",
                        "ignore-extra-warning",
                        "ignore-extra-warning-2",
                    ],
                    "updated_at": "2026-06-01T00:00:00+00:00",
                    "as_of": "2026-06-01",
                    "selection_penalties": ["freshness:stale"],
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    assert page["freshness"] == "stale"
    assert page["quality_status"] == "weak"
    assert page["quality_warnings"] == [
        "valuation_stale_gt_30d",
        "price_missing",
        "ignore-extra-warning",
    ]
    assert page["updated_at"] == "2026-06-01T00:00:00+00:00"
    assert page["as_of"] == "2026-06-01"


def test_market_judgment_page_preserves_guidance_metadata() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-page-guidance",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.regime.pullback",
                    "rank": 1,
                    "score": 92.0,
                    "usage_guidance": {
                        "risk_posture": "patient_waiting_entry",
                        "required_cross_checks": [
                            "live_price_location",
                            "market_pulse",
                        ],
                        "max_confidence_without_cross_check": 0.6,
                    },
                    "usage_guidance_effectiveness": {
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "patient_waiting_entry"
                                ),
                                "status": "active",
                                "sample_count": 8,
                            }
                        ]
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "flow_missing",
                            "status": "degraded",
                            "sample_count": 3,
                        }
                    ],
                    "quality_warning_effectiveness_statuses": ["degraded"],
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    assert page["usage_guidance"]["risk_posture"] == "patient_waiting_entry"
    assert page["usage_guidance"]["max_confidence_without_cross_check"] == 0.6
    assert page["usage_guidance_effectiveness"]["metrics"][0]["status"] == "active"
    assert page["quality_warning_effectiveness"][0]["warning"] == "flow_missing"
    assert page["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_market_judgment_observe_prompt_preserves_effectiveness_attention_items() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}
    attention_items = [
        {
            "page_id": "kis.symbol.005930",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.patient_waiting_entry",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "valuation_stale_gt_30d",
        },
    ]

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:observe-attention-items",
            "target_scope": "kis",
            "prompt_mode": "observe",
            "effectiveness_attention_items": attention_items,
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 92.0,
                }
            ],
        },
        max_chars=4_000,
    )

    observation = prompt["jue_wiki_selection_observation"]
    application = prompt["jue_wiki_application"]
    assert observation["effectiveness_attention_items"] == attention_items
    assert application["effectiveness_attention_items"] == attention_items


def test_market_judgment_assist_prompt_compacts_effectiveness_attention_items() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:assist-attention-items",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "effectiveness_attention_items": [
                {
                    "page_id": "kis.symbol.005930",
                    "kind": "usage_guidance",
                    "status": "active",
                    "evidence_id": (
                        "usage_guidance.risk_posture.patient_waiting_entry"
                    ),
                    "verbose_note": "DROP_ME" * 100,
                    "sample_count": 99,
                },
                {
                    "page_id": "kis.symbol.005930",
                    "kind": "quality_warning",
                    "status": "degraded",
                    "warning": "valuation_stale_gt_30d",
                    "raw_payload": {"drop": True},
                },
            ],
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 92.0,
                }
            ],
        },
        max_chars=4_000,
    )

    compact_items = [
        {
            "page_id": "kis.symbol.005930",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.patient_waiting_entry",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "valuation_stale_gt_30d",
        },
    ]
    assert prompt["jue_wiki"]["effectiveness_attention_items"] == compact_items
    assert prompt["jue_wiki_application"]["effectiveness_attention_items"] == (
        compact_items
    )


def test_market_judgment_derives_effectiveness_attention_items_from_page_rows() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:row-derived-attention-items",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 92.0,
                    "usage_guidance_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "patient_waiting_entry"
                                ),
                                "status": "active",
                                "sample_count": 7,
                            }
                        ],
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "valuation_stale_gt_30d",
                            "status": "degraded",
                            "sample_count": 4,
                        }
                    ],
                }
            ],
        },
        max_chars=4_000,
    )

    expected_items = [
        {
            "page_id": "kis.symbol.005930",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.patient_waiting_entry",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "valuation_stale_gt_30d",
        },
    ]
    assert prompt["jue_wiki"]["effectiveness_attention_items"] == expected_items
    assert prompt["jue_wiki_application"]["effectiveness_attention_items"] == (
        expected_items
    )


def test_market_judgment_observe_derives_effectiveness_attention_items_from_page_rows() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:observe-row-derived-attention-items",
            "target_scope": "kis",
            "prompt_mode": "observe",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 92.0,
                    "usage_guidance_effectiveness": {
                        "status": "active",
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "patient_waiting_entry"
                                ),
                                "status": "active",
                                "sample_count": 7,
                            }
                        ],
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "valuation_stale_gt_30d",
                            "status": "degraded",
                            "sample_count": 4,
                        }
                    ],
                }
            ],
        },
        max_chars=4_000,
    )

    expected_items = [
        {
            "page_id": "kis.symbol.005930",
            "kind": "usage_guidance",
            "status": "active",
            "evidence_id": "usage_guidance.risk_posture.patient_waiting_entry",
        },
        {
            "page_id": "kis.symbol.005930",
            "kind": "quality_warning",
            "status": "degraded",
            "warning": "valuation_stale_gt_30d",
        },
    ]
    observation = prompt["jue_wiki_selection_observation"]
    assert observation["effectiveness_attention_items"] == expected_items
    assert prompt["jue_wiki_application"]["effectiveness_attention_items"] == (
        expected_items
    )


def test_market_judgment_jue_wiki_prompt_context_keeps_requested_symbol_quality_metadata() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-symbol-quality",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "title": "삼성전자",
                    "freshness": "stale",
                    "quality_status": "weak",
                    "quality_warnings": [
                        "valuation_stale_gt_30d",
                        "price_missing",
                        "ignore-extra-warning",
                        "ignore-extra-warning-2",
                    ],
                    "updated_at": "2026-06-01T00:00:00+00:00",
                    "as_of": "2026-06-01",
                    "summary": "KEEP_SUMMARY. " + ("DROP_ME" * 100),
                }
            ],
        },
        max_chars=4_000,
    )

    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert summary["freshness"] == "stale"
    assert summary["quality_status"] == "weak"
    assert summary["quality_warnings"] == [
        "valuation_stale_gt_30d",
        "price_missing",
        "ignore-extra-warning",
    ]
    assert summary["updated_at"] == "2026-06-01T00:00:00+00:00"
    assert summary["as_of"] == "2026-06-01"


def test_market_judgment_requested_symbol_summary_preserves_guidance_metadata() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-requested-guidance",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "title": "삼성전자",
                    "summary": "장중 고점 추격보다 눌림 확인 후 판단",
                    "usage_guidance": {
                        "risk_posture": "patient_waiting_entry",
                        "required_cross_checks": [
                            "live_price_location",
                            "foreign_flow",
                            "market_pulse",
                        ],
                        "max_confidence_without_cross_check": 0.55,
                    },
                    "usage_guidance_effectiveness": {
                        "metrics": [
                            {
                                "page_id": (
                                    "usage_guidance.risk_posture."
                                    "patient_waiting_entry"
                                ),
                                "status": "active",
                                "sample_count": 7,
                                "avg_return_pct": 1.1,
                            }
                        ],
                        "summary": "장중 눌림 대기 지침은 추격 손절을 줄임",
                    },
                    "quality_warning_source_effectiveness": {
                        "metrics": [
                            {
                                "page_id": "kis.symbol.005930",
                                "source_type": "symbol_fundamentals",
                                "source_id": "005930:valuation",
                                "warning": "valuation_stale_gt_30d",
                                "status": "degraded",
                                "sample_count": 4,
                            }
                        ]
                    },
                    "quality_warning_effectiveness": [
                        {
                            "warning": "valuation_stale_gt_30d",
                            "status": "degraded",
                            "sample_count": 4,
                            "avg_return_pct": -0.6,
                        }
                    ],
                    "quality_warning_effectiveness_statuses": ["degraded"],
                }
            ],
        },
        max_chars=4_000,
    )

    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert summary["usage_guidance"]["risk_posture"] == "patient_waiting_entry"
    assert summary["usage_guidance"]["required_cross_checks"] == [
        "live_price_location",
        "foreign_flow",
        "market_pulse",
    ]
    assert summary["usage_guidance"]["max_confidence_without_cross_check"] == 0.55
    assert summary["usage_guidance_effectiveness"]["metrics"][0]["status"] == "active"
    assert (
        summary["quality_warning_source_effectiveness"]["metrics"][0]["source_type"]
        == "symbol_fundamentals"
    )
    assert summary["quality_warning_effectiveness"][0]["warning"] == (
        "valuation_stale_gt_30d"
    )
    assert summary["quality_warning_effectiveness_statuses"] == ["degraded"]


def test_market_judgment_jue_wiki_prompt_context_derives_quality_metadata_from_evidence_quality() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    evidence_quality = {
        "status_counts": {"weak": 1},
        "top_warnings": [
            {"warning": "valuation_stale_gt_30d", "count": 2},
            {"warning": "price_missing", "count": 1},
            {"warning": "ignore-extra-warning", "count": 1},
            {"warning": "ignore-extra-warning-2", "count": 1},
        ],
    }
    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-evidence-quality",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 87.5,
                    "evidence_quality": evidence_quality,
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "freshness": "stale",
                    "summary": "KEEP_SUMMARY.",
                    "evidence_quality": evidence_quality,
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert page["quality_status"] == "weak"
    assert summary["quality_status"] == "weak"
    assert page["quality_warnings"] == [
        "valuation_stale_gt_30d",
        "price_missing",
        "ignore-extra-warning",
    ]
    assert summary["quality_warnings"] == [
        "valuation_stale_gt_30d",
        "price_missing",
        "ignore-extra-warning",
    ]


def test_market_judgment_jue_wiki_prompt_context_canonicalizes_nested_evidence_quality_aliases() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    evidence_quality = {
        "status_counts": {"ok": 1, "degraded": 1},
        "top_warnings": [{"warning": "source_error", "count": 1}],
    }
    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-evidence-quality-aliases",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "score": 87.5,
                    "evidence_quality": evidence_quality,
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "summary": "KEEP_SUMMARY.",
                    "evidence_quality": evidence_quality,
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert page["quality_status"] == "weak"
    assert summary["quality_status"] == "weak"
    assert page["evidence_quality"]["status_counts"] == {"strong": 1, "weak": 1}
    assert summary["evidence_quality"]["status_counts"] == {"strong": 1, "weak": 1}


def test_market_judgment_jue_wiki_prompt_context_canonicalizes_direct_quality_status_aliases() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-direct-quality-aliases",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "rank": 1,
                    "quality_status": "degraded",
                    "source_refs": [
                        {
                            "source_type": "symbol_fundamentals",
                            "source_id": "005930:alias-source",
                            "quality_status": "degraded",
                            "quality_warnings": ["source_error"],
                            "evidence_quality": {
                                "status_counts": {"ok": 1, "degraded": 1},
                                "top_warnings": [
                                    {"warning": "source_error", "count": 1}
                                ],
                            },
                            "raw_blob": "drop-me",
                        }
                    ],
                }
            ],
            "requested_symbol_summaries": [
                {
                    "symbol": "005930",
                    "page_id": "kis.symbol.005930",
                    "summary": "KEEP_SUMMARY.",
                    "quality_status": "ok",
                }
            ],
        },
        max_chars=4_000,
    )

    page = prompt["jue_wiki"]["pages"][0]
    summary = prompt["jue_wiki"]["requested_symbol_summaries"][0]
    assert page["quality_status"] == "weak"
    assert summary["quality_status"] == "strong"
    source_ref = page["source_refs"][0]
    assert source_ref["quality_status"] == "weak"
    assert source_ref["evidence_quality"]["status_counts"] == {
        "strong": 1,
        "weak": 1,
    }
    assert "raw_blob" not in source_ref


def test_market_judgment_jue_wiki_application_metadata_summarizes_quality_pressure() -> None:
    from tradecraft.services.market_judgment import _attach_jue_wiki_prompt_context

    prompt: dict[str, object] = {"symbols": [{"symbol": "005930"}]}

    _attach_jue_wiki_prompt_context(
        prompt,
        {
            "status": "ok",
            "selection_run_id": "selection:market-quality-summary",
            "target_scope": "kis",
            "prompt_mode": "assist",
            "pages": [
                {
                    "page_id": "kis.symbol.005930",
                    "quality_status": "weak",
                    "quality_warnings": [
                        "valuation_stale_gt_30d",
                        "price_missing",
                    ],
                }
            ],
            "requested_symbol_summaries": [
                {
                    "page_id": "kis.symbol.277810",
                    "symbol": "277810",
                    "quality_status": "partial",
                    "quality_warnings": ["price_missing"],
                    "summary": "레인보우로보틱스 압축 기억",
                }
            ],
        },
        max_chars=4_000,
    )

    assert prompt["jue_wiki_application"]["quality_summary"] == {
        "row_count": 2,
        "status_counts": {"partial": 1, "weak": 1},
        "warning_counts": {"price_missing": 2, "valuation_stale_gt_30d": 1},
        "top_warnings": [
            {"warning": "price_missing", "count": 2},
            {"warning": "valuation_stale_gt_30d", "count": 1},
        ],
        "warning_page_ids": {
            "price_missing": ["kis.symbol.005930", "kis.symbol.277810"],
            "valuation_stale_gt_30d": ["kis.symbol.005930"],
        },
        "weak_page_ids": ["kis.symbol.005930"],
        "caution_page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
    }
    assert prompt["jue_wiki_application"]["quality_pressure_action_plan"] == {
        "status": "repair_required",
        "hard_blocker": False,
        "decision_policy": (
            "use_quality_warnings_as_candidate_level_cross_checks_not_blanket_holds"
        ),
        "required_adjustments": [
            {
                "adjustment_type": "candidate_level_cross_check",
                "reason": "weak_wiki_pages",
                "page_ids": ["kis.symbol.005930"],
                "resolution": "refresh_or_cross_check_before_sizing",
            },
                {
                    "adjustment_type": "quality_warning_resolution",
                    "warning": "price_missing",
                    "count": 2,
                    "page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
                    "resolution": "refresh_or_cross_check_before_sizing",
                },
                {
                    "adjustment_type": "quality_warning_resolution",
                    "warning": "valuation_stale_gt_30d",
                    "count": 1,
                    "page_ids": ["kis.symbol.005930"],
                    "resolution": "refresh_or_cross_check_before_sizing",
                },
            ],
            "repair_focus": [
                {
                    "priority_type": "evidence_quality",
                    "warning": "price_missing",
                    "count": 2,
                    "page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
                    "decision_use": "evidence_quality_cross_check",
                },
                {
                    "priority_type": "evidence_quality",
                    "warning": "valuation_stale_gt_30d",
                    "count": 1,
                    "page_ids": ["kis.symbol.005930"],
                    "decision_use": "evidence_quality_cross_check",
                },
            ],
        "caution_page_ids": ["kis.symbol.005930", "kis.symbol.277810"],
    }
