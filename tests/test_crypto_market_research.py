from __future__ import annotations

import asyncio
import base64
import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services.crypto_market_research import (
    CryptoMarketResearchConfig,
    CryptoMarketResearchService,
    _compact_research_packet,
)
from tradecraft.runtime.crypto_market_research_runner import parse_kline_intervals


def _decode_gzip_base64(value: str) -> str:
    assert value.startswith("gzip+base64:")
    return gzip.decompress(base64.b64decode(value.removeprefix("gzip+base64:"))).decode(
        "utf-8"
    )


class _FakeBinance:
    async def fetch_24h_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": market,
            "change_pct_24h": 3.0,
            "quote_volume": 2_000_000,
            "raw": {},
        }

    async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": market,
            "bid": 99.9,
            "ask": 100.1,
            "spread_bps": 20.0,
            "raw": {},
        }

    async def fetch_klines(
        self,
        symbol: str,
        *,
        market: str = "spot",
        interval: str = "1m",
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        return [
            {
                "open_time": idx,
                "open": 100 + idx,
                "high": 101 + idx,
                "low": 99 + idx,
                "close": 100 + idx,
                "volume": 10 + idx,
                "quote_volume": 1000 + idx,
                "close_time": idx + 1,
                "raw": [],
            }
            for idx in range(20)
        ]

    async def fetch_futures_premium_index(self, symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "mark_price": 120,
            "index_price": 119,
            "funding_rate": 0.0003,
            "next_funding_time": 123,
            "raw": {},
        }

    async def fetch_futures_open_interest(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "open_interest": 12345, "raw": {}}


class _FakeUniverseBinance(_FakeBinance):
    async def fetch_24h_tickers(self, *, market: str = "spot") -> list[dict[str, Any]]:
        assert market == "spot"
        return [
            {"symbol": "ETHUSDT", "quote_volume": 900_000_000},
            {"symbol": "PEPEUSDT", "quote_volume": 700_000_000},
            {"symbol": "USDCUSDT", "quote_volume": 650_000_000},
            {"symbol": "SOLUSDT", "quote_volume": 600_000_000},
            {"symbol": "BTCUPUSDT", "quote_volume": 550_000_000},
            {"symbol": "DOGEUSDT", "quote_volume": 500_000_000},
            {"symbol": "ILLQUSDT", "quote_volume": 1_000},
        ]


class _FakeSpark:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, prompt: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        return {
            "symbol_notes": [
                {
                    "symbol": "BTCUSDT",
                    "stance": "long_watch",
                    "horizon": "swing",
                    "confidence": 0.72,
                    "summary_md": "BTC는 유동성과 추세가 우세하다.",
                    "reasons": ["1m 추세 상승", "거래대금 충분"],
                    "risks": ["funding 과열"],
                    "triggers": ["전고점 돌파"],
                }
            ],
            "candidates": [
                {
                    "symbol": "BTCUSDT",
                    "market": "spot",
                    "stance": "long_watch",
                    "horizon": "swing",
                    "score": 78,
                    "confidence": 0.72,
                    "reason_md": "추세와 유동성 우세",
                    "block_template": {"target_price": 110, "stop_price": 95},
                }
            ],
        }


class _FailingSpark:
    resolved_model = "gpt-5.5"

    async def complete_json(self, prompt: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "spark unavailable"}


class _FuturesUnavailableBinance(_FakeBinance):
    async def fetch_futures_premium_index(self, symbol: str) -> dict[str, Any]:
        raise RuntimeError("futures premium unavailable")

    async def fetch_futures_open_interest(self, symbol: str) -> dict[str, Any]:
        raise RuntimeError("futures open interest unavailable")


class _MissingBookBinance(_FakeBinance):
    async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": market,
            "bid": None,
            "ask": None,
            "spread_bps": 0,
            "source": "fake_missing_book",
            "fetched_at": "2026-05-26T00:00:00+00:00",
            "raw": {},
        }


class _CrossedBookBinance(_FakeBinance):
    async def fetch_book_ticker(self, symbol: str, *, market: str = "spot") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "market": market,
            "bid": 101.0,
            "ask": 100.0,
            "spread_bps": 0,
            "source": "fake_crossed_book",
            "fetched_at": "2026-05-26T00:01:00+00:00",
            "raw": {},
        }


def test_research_prompt_compacts_repeated_policy_rule_evaluation() -> None:
    repeated_rules = [
        {
            "policy_id": f"validation.binance.rule_{idx}",
            "rule_id": f"validation.binance.rule_{idx}@v12345",
            "action": "caution",
            "status": "active_caution",
            "version": 12345,
            "reason": "반복 정책 설명 " * 80,
            "effect": {
                "action": "caution",
                "policy_mode": "soft_data_rule",
                "entry_bias": "fractional_kelly_probe_entry",
                "sizing_policy": "no_size_increase_until_scale_blockers_clear",
                "target_stop_review": "rebuild_scale_repair_targets_before_pressing",
                "risk_note": "매우 긴 위험 설명 " * 80,
                "required_evidence": ["lane_scorecard", "cost_evidence", "entry_quality"],
                "hard_filter": False,
                "safety_gate_override": False,
            },
        }
        for idx in range(8)
    ]
    packet = {
        "generated_at": "2026-06-18T15:43:57Z",
        "symbols": [f"SYM{idx}USDT" for idx in range(40)],
        "observed_symbol_count": 300,
        "focus_symbol_count": 40,
        "market_context": {
            "status": "ok",
            "items": [],
            "candidates": [],
            "symbol_notes": [],
        },
        "memory_context": {
            "status": "ok",
            "policy_rule_evaluation": {
                "status": "ok",
                "active_rule_count": 8,
                "applied_count": 240,
                "global": repeated_rules,
                "by_symbol": {f"SYM{idx}USDT": repeated_rules for idx in range(40)},
            },
        },
    }

    compact = _compact_research_packet(packet)
    policy = compact["memory_context"]["policy_rule_evaluation"]
    encoded = json.dumps(compact, ensure_ascii=False)

    assert len(encoded) < 25_000
    assert policy["by_symbol_count"] == 40
    assert len(policy["by_symbol"]) == 12
    assert len(policy["by_symbol"][0]["top_rules"]) == 3
    assert "risk_note" not in json.dumps(policy["by_symbol"][0], ensure_ascii=False)


def test_parse_kline_intervals_normalizes_config_string() -> None:
    assert parse_kline_intervals("1m:120, 5m:96;bad, 1h:0, 4h:180") == {
        "1m": 120,
        "5m": 96,
        "4h": 180,
    }


def test_crypto_research_db_schema_initializes(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    status = service.status()

    assert status["status"] == "ok"
    assert status["db_path"].endswith("crypto.db")
    assert status["snapshot_count"] == 0
    assert status["candidate_count"] == 0


def test_crypto_research_candidate_packets_include_alpha_score(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    packets = service._build_candidate_packets(
        items=[
            {
                "symbol": "MEMEUSDT",
                "features": {
                    "change_pct_24h": 18.0,
                    "volume_expansion_ratio": 3.2,
                    "spread_bps": 15.0,
                    "orderbook_depth_usdt": 150_000.0,
                    "squeeze_risk_score": 74.0,
                },
            }
        ],
        candidates=[],
    )

    alpha_score = packets["top_movers"][0]["alpha_score_v3"]
    assert alpha_score["version"] == "crypto_alpha_score_v3"
    assert alpha_score["total_score"] > 70


def test_crypto_research_prompt_uses_native_schema_safe_arrays_and_price_template(
    tmp_path: Path,
) -> None:
    from tradecraft.services.codex_native import CodexNativeConfig, CodexNativeRuntime

    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    prompt = service._build_research_prompt({"symbols": ["BTCUSDT"], "items": []})
    note_schema = prompt["output_schema"]["symbol_notes"][0]
    candidate_schema = prompt["output_schema"]["candidates"][0]

    assert note_schema["reasons"] == ["string"]
    assert note_schema["risks"] == ["string"]
    assert note_schema["triggers"] == ["string"]
    assert candidate_schema["block_template"]["entry_price"] == 0.0
    assert candidate_schema["block_template"]["target_price"] == 0.0
    assert candidate_schema["block_template"]["stop_price"] == 0.0
    assert candidate_schema["block_template"]["evidence_refs"] == ["string"]
    assert "contracts" not in prompt["jue_workflow"]
    assert "reference_contracts" in prompt["jue_workflow"]
    assert prompt["native_thread_mode"] == "ephemeral"
    assert prompt["telemetry"] == {
        "component": "crypto_market_research",
        "operation": "run_research_once",
    }

    native_schema = CodexNativeRuntime(
        CodexNativeConfig(usage_enabled=False)
    )._native_output_schema(prompt)
    assert native_schema is not None
    assert "symbol_notes" in native_schema["properties"]
    assert "candidates" in native_schema["properties"]


def test_collect_phase1_market_structure_builds_features(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=_FakeBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)

    assert result["status"] == "ok"
    assert result["collected_count"] == 1
    collected_features = result["items"][0]["features"]
    assert context["items"][0]["symbol"] == "BTCUSDT"
    features = context["items"][0]["features"]
    assert collected_features["bid_price"] == pytest.approx(99.9)
    assert collected_features["ask_price"] == pytest.approx(100.1)
    assert collected_features["book_fresh"] is True
    assert features["trend_1m"] == "up"
    assert features["quote_volume_usdt"] == pytest.approx(2_000_000)
    assert features["funding_rate"] == pytest.approx(0.0003)
    assert features["bid_price"] == pytest.approx(99.9)
    assert features["ask_price"] == pytest.approx(100.1)
    assert features["spread_bps"] == pytest.approx(20.0)
    assert features["book_source"] == "book_ticker"
    assert features["book_fresh"] is True
    assert features["book_fetched_at"]


def test_collect_market_structure_times_out_slow_symbol(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto.db"),
            collect_symbol_timeout_sec=1.0,
        ),
        binance=_FakeBinance(),
    )

    async def slow_collect(symbol: str) -> dict[str, Any]:
        _ = symbol
        await asyncio.sleep(1.2)
        return {}

    service._collect_symbol_market = slow_collect  # type: ignore[method-assign]

    result = asyncio.run(service.collect_market_structure(["SLOWUSDT"]))

    assert result["status"] == "error"
    assert result["collected_count"] == 0
    assert result["errors"][0]["symbol"] == "SLOWUSDT"
    assert result["errors"][0]["error_message"] == "collect_symbol_timeout"


def test_collect_market_structure_marks_missing_book_not_fresh(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=_MissingBookBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)
    features = context["items"][0]["features"]

    assert result["status"] == "ok"
    assert result["items"][0]["features"]["book_fresh"] is False
    assert features["bid_price"] == pytest.approx(0.0)
    assert features["ask_price"] == pytest.approx(0.0)
    assert features["spread_bps"] == pytest.approx(0.0)
    assert features["book_source"] == "fake_missing_book"
    assert features["book_fetched_at"] == "2026-05-26T00:00:00+00:00"
    assert features["book_fresh"] is False


def test_collect_market_structure_marks_crossed_book_not_fresh(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=_CrossedBookBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)
    features = context["items"][0]["features"]

    assert result["status"] == "ok"
    assert result["items"][0]["features"]["book_fresh"] is False
    assert features["bid_price"] == pytest.approx(0.0)
    assert features["ask_price"] == pytest.approx(0.0)
    assert features["spread_bps"] == pytest.approx(0.0)
    assert features["book_source"] == "fake_crossed_book"
    assert features["book_fetched_at"] == "2026-05-26T00:01:00+00:00"
    assert features["book_fresh"] is False


def test_collect_market_structure_persists_quant_signals(tmp_path: Path) -> None:
    from tradecraft.services.crypto_quant import CryptoQuantRepository

    quant_repo = CryptoQuantRepository(str(tmp_path / "quant.db"))
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=_FakeBinance(),
        quant_repository=quant_repo,
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    signals = quant_repo.latest_signals(symbols=["BTCUSDT"], limit=5)
    context = service.latest_context(symbols=["BTCUSDT"], limit=5)

    assert result["status"] == "ok"
    assert signals
    assert signals[0]["symbol"] == "BTCUSDT"
    assert signals[0]["horizon"] in {"scalp", "intraday", "swing"}
    assert "metrics" in signals[0]["signal"]
    assert context["quant"]["status"] == "ok"
    assert context["quant"]["items"]


def test_crypto_market_research_context_includes_normalized_evidence(
    tmp_path: Path,
) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    run_id = service.repository.save_research_run(
        status="ok",
        mode="test",
        model="fixture",
        prompt={},
        response={},
        error_message="",
    )
    service.repository.upsert_candidate(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "stance": "long_watch",
            "horizon": "swing",
            "score": 78,
            "confidence": 0.72,
            "reason_md": "trend and liquidity",
        },
        source_run_id=run_id,
    )

    context = service.latest_context(limit=10)

    assert context["evidence"][0]["source"] == "crypto_market_research"
    assert context["evidence"][0]["symbol"] == "BTCUSDT"
    assert context["evidence"][0]["signal_type"] == "research_candidate"


def test_crypto_research_keeps_spot_and_futures_candidates_for_same_symbol(
    tmp_path: Path,
) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    run_id = service.repository.save_research_run(
        status="ok",
        mode="test",
        model="fixture",
        prompt={},
        response={},
        error_message="",
    )

    service.repository.upsert_candidate(
        {
            "symbol": "BTCUSDT",
            "market": "spot",
            "stance": "long_watch",
            "horizon": "swing",
            "score": 72,
            "confidence": 0.61,
            "reason_md": "spot accumulation lane",
        },
        source_run_id=run_id,
    )
    service.repository.upsert_candidate(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "stance": "short_watch",
            "horizon": "intraday",
            "score": 76,
            "confidence": 0.66,
            "reason_md": "futures hedge lane",
        },
        source_run_id=run_id,
    )

    candidates = service.repository.latest_candidates(symbols=["BTCUSDT"], limit=10)

    lanes = {(row["market"], row["stance"], row["horizon"]) for row in candidates}
    assert lanes == {
        ("spot", "long_watch", "swing"),
        ("futures", "short_watch", "intraday"),
    }


def test_crypto_research_latest_candidates_filters_stale_rows(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    run_id = service.repository.save_research_run(
        status="ok",
        mode="test",
        model="fixture",
        prompt={},
        response={},
        error_message="",
    )
    service.repository.upsert_candidate(
        {
            "symbol": "OLDUSDT",
            "market": "futures",
            "stance": "short_watch",
            "horizon": "intraday",
            "score": 99,
            "confidence": 0.8,
            "reason_md": "stale high score",
        },
        source_run_id=run_id,
    )
    with service.repository._connect() as conn:
        conn.execute(
            "UPDATE crypto_candidates SET updated_at = ? WHERE symbol = ?",
            ("2026-01-01T00:00:00+00:00", "OLDUSDT"),
        )

    assert service.repository.latest_candidates(limit=10) == []
    historical = service.repository.latest_candidates(limit=10, max_age_sec=0)
    assert historical[0]["symbol"] == "OLDUSDT"


def test_collect_builds_multi_timeframe_features(tmp_path: Path) -> None:
    class MultiFrameBinance(_FakeBinance):
        async def fetch_klines(
            self,
            symbol: str,
            *,
            market: str = "spot",
            interval: str = "1m",
            limit: int = 120,
        ) -> list[dict[str, Any]]:
            closes = {
                "1m": [100, 101, 102, 103],
                "5m": [100, 99, 98, 97],
                "15m": [100, 100, 100, 100],
                "1h": [100, 105, 110, 115],
                "4h": [120, 115, 110, 105],
            }[interval]
            return [
                {
                    "open_time": idx,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 10,
                    "quote_volume": 1000,
                    "close_time": idx + 1,
                    "raw": [],
                }
                for idx, close in enumerate(closes)
            ]

    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto.db"),
            kline_intervals={"1m": 4, "5m": 4, "15m": 4, "1h": 4, "4h": 4},
        ),
        binance=MultiFrameBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)
    features = context["items"][0]["features"]

    assert result["status"] == "ok"
    assert features["timeframes"]["1m"]["trend"] == "up"
    assert features["timeframes"]["5m"]["trend"] == "down"
    assert features["timeframes"]["15m"]["trend"] == "flat"
    assert features["timeframes"]["1h"]["momentum_pct"] == pytest.approx(15.0)
    assert features["timeframe_alignment"] in {"mixed", "bullish", "bearish"}


def test_market_regime_uses_btc_and_breadth(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    for symbol, change, alignment in [
        ("BTCUSDT", -4.0, "bearish"),
        ("ETHUSDT", -5.0, "bearish"),
        ("SOLUSDT", -6.0, "bearish"),
        ("BNBUSDT", -1.0, "mixed"),
    ]:
        service.repository.upsert_features(
            symbol,
            {
                "symbol": symbol,
                "change_pct_24h": change,
                "quote_volume_usdt": 10_000_000,
                "timeframe_alignment": alignment,
            },
        )

    regime = service.compute_market_regime(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    )
    context = service.latest_context(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
        limit=4,
    )

    assert regime["status"] == "ok"
    assert regime["regime"] == "risk_off_downtrend"
    assert regime["btc_change_pct_24h"] == pytest.approx(-4.0)
    assert regime["bearish_breadth_pct"] >= 75.0
    assert context["market_regime"]["regime"] == "risk_off_downtrend"
    assert context["observed_symbol_count"] == 4


def test_latest_context_includes_regime_brief_with_narrative_bias(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    for symbol, change, alignment, funding in [
        ("BTCUSDT", -3.4, "bearish", -0.0006),
        ("ETHUSDT", -2.2, "bearish", -0.0002),
        ("SOLUSDT", 4.8, "bullish", 0.0001),
        ("BNBUSDT", -0.3, "mixed", 0.0),
    ]:
        service.repository.upsert_features(
            symbol,
            {
                "symbol": symbol,
                "change_pct_24h": change,
                "quote_volume_usdt": 10_000_000,
                "timeframe_alignment": alignment,
                "trend_1m": "down" if change < 0 else "up",
                "funding_rate": funding,
                "mark_index_basis_pct": -0.12 if symbol == "BTCUSDT" else 0.0,
                "squeeze_risk": "short_squeeze" if symbol == "BTCUSDT" else "normal",
                "squeeze_risk_score": 65 if symbol == "BTCUSDT" else 10,
                "entry_quality": "conditional",
            },
        )
    service.repository.upsert_candidate(
        {
            "symbol": "BTCUSDT",
            "market": "futures",
            "stance": "short_watch",
            "horizon": "intraday",
            "score": 74,
            "confidence": 0.68,
            "reason_md": "risk-off pressure",
            "block_template": {},
        },
        source_run_id=1,
    )
    service.repository.upsert_candidate(
        {
            "symbol": "SOLUSDT",
            "market": "spot",
            "stance": "long_watch",
            "horizon": "swing",
            "score": 69,
            "confidence": 0.62,
            "reason_md": "relative strength",
            "block_template": {},
        },
        source_run_id=1,
    )
    service.save_external_context(
        source_id="fear_greed",
        key="BTC",
        payload={"value": 29, "value_classification": "Fear"},
    )

    context = service.latest_context(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
        limit=4,
    )
    brief = context["regime_brief"]

    assert brief["version"] == "crypto_regime_brief_v1"
    assert brief["regime"] in {"risk_off_downtrend", "high_dispersion_chop"}
    assert brief["market_direction"] in {"risk_off_downtrend", "dispersion_chop"}
    assert brief["risk_posture"] in {"defensive", "selective"}
    assert brief["lane_bias"]["futures_short"]
    assert brief["horizon_bias"]["mid"]
    assert brief["major_rows"][0]["symbol"] == "BTCUSDT"
    assert brief["external_notes"][0]["source_id"] == "fear_greed"
    assert brief["derivatives_notes"][0]["symbol"] == "BTCUSDT"
    assert "현물 롱" in brief["operator_summary_ko"]


def test_latest_context_separates_observed_universe_from_focus_rows(
    tmp_path: Path,
) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    for index in range(12):
        service.repository.upsert_features(
            f"ALT{index:03d}USDT",
            {
                "symbol": f"ALT{index:03d}USDT",
                "score": 100 - index,
                "quote_volume_usdt": 10_000_000 - index,
                "change_pct_24h": index,
            },
        )

    context = service.latest_context(limit=5)

    assert context["observed_symbol_count"] == 12
    assert context["focus_symbol_count"] == 5
    assert len(context["items"]) == 5


def test_latest_context_does_not_persist_regime_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    service.repository.upsert_features(
        "BTCUSDT",
        {
            "symbol": "BTCUSDT",
            "change_pct_24h": 4.0,
            "quote_volume_usdt": 10_000_000,
            "timeframe_alignment": "bullish",
        },
    )

    before = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM crypto_regime_snapshots"
    ).fetchone()[0]
    context = service.latest_context(symbols=["BTCUSDT"], limit=1)
    after = sqlite3.connect(db_path).execute(
        "SELECT COUNT(*) FROM crypto_regime_snapshots"
    ).fetchone()[0]

    assert context["market_regime"]["status"] == "ok"
    assert after == before


def test_save_klines_compacts_exchange_raw_arrays(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    raw_kline = [
        1782316800000,
        "60250.00000000",
        "60678.10000000",
        "59102.70000000",
        "59958.30000000",
        "10843.62240000",
        1782331199999,
        "648464612.98165350",
        1716720,
        "4874.63052000",
        "291609263.21429200",
        "0",
    ]

    service.repository.save_klines(
        symbol="BTCUSDT",
        market="spot",
        interval="4h",
        rows=[
            {
                "open_time": raw_kline[0],
                "open": raw_kline[1],
                "high": raw_kline[2],
                "low": raw_kline[3],
                "close": raw_kline[4],
                "volume": raw_kline[5],
                "quote_volume": raw_kline[7],
                "close_time": raw_kline[6],
                "raw": raw_kline,
            }
        ],
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT open, high, low, close, volume, quote_volume, close_time, raw_json
            FROM crypto_klines
            WHERE symbol = 'BTCUSDT'
            """
        ).fetchone()

    assert row[:7] == (
        60250.0,
        60678.1,
        59102.7,
        59958.3,
        10843.6224,
        648464612.9816535,
        1782331199999,
    )
    raw = json.loads(row[7])
    assert raw["_raw_compacted"] is True
    assert raw["_raw_type"] == "exchange_kline_array"
    assert raw["_raw_item_count"] == len(raw_kline)
    assert len(row[7]) < len(json.dumps(raw_kline))


def test_crypto_market_research_prunes_old_timeseries(tmp_path: Path) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    old_at = "2020-01-01T00:00:00+00:00"
    service.repository.save_market_snapshot(
        symbol="BTCUSDT",
        market="spot",
        ticker={"price": 100, "quote_volume": 1, "change_pct_24h": 0},
        book={"bid": 99, "ask": 101, "spread_bps": 20},
        captured_at=old_at,
    )
    service.repository.save_klines(
        symbol="BTCUSDT",
        market="spot",
        interval="1m",
        rows=[
            {
                "open_time": 1,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
                "quote_volume": 100,
                "close_time": 1,
            }
        ],
    )
    service.repository.save_klines(
        symbol="HOTUSDT",
        market="spot",
        interval="1m",
        rows=[
            {
                "open_time": 2,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
                "quote_volume": 100,
                "close_time": 0,
            }
        ],
    )
    service.repository.save_derivatives(
        symbol="BTCUSDT",
        premium={"mark_price": 100, "index_price": 100, "funding_rate": 0},
        open_interest={"open_interest": 1},
        captured_at=old_at,
    )
    service.repository.save_regime_snapshot({"regime": "old"})
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE crypto_regime_snapshots SET captured_at = ?", (old_at,))

    result = service.prune_history(market_retention_days=30, quant_retention_days=30)

    assert result["market"]["market_snapshots_deleted"] == 1
    assert result["market"]["derivatives_deleted"] == 1
    assert result["market"]["klines_deleted"] == 1
    assert result["market"]["archived"]["crypto_market_snapshots"] == 1
    assert result["market"]["archived"]["crypto_derivatives"] == 1
    assert result["market"]["archived"]["crypto_regime_snapshots"] == 1
    assert result["market"]["archived"]["crypto_klines"] == 1
    kline_retention = result["market"]["retention"]["tables"]["crypto_klines"]
    assert kline_retention["archive_table"] == "crypto_klines_archive"
    assert kline_retention["compressed_columns"] == ["raw_json"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_market_snapshots_archive"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_derivatives_archive"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_klines_archive"
        ).fetchone()[0] == 1
        archived_market_raw = conn.execute(
            "SELECT raw_json FROM crypto_market_snapshots_archive"
        ).fetchone()[0]
        archived_derivative_raw = conn.execute(
            "SELECT raw_json FROM crypto_derivatives_archive"
        ).fetchone()[0]
        archived_kline_raw = conn.execute(
            "SELECT raw_json FROM crypto_klines_archive"
        ).fetchone()[0]
        archived_regime_payload = conn.execute(
            "SELECT payload_json FROM crypto_regime_snapshots_archive"
        ).fetchone()[0]
        remaining_klines = conn.execute(
            "SELECT symbol, close_time FROM crypto_klines ORDER BY symbol"
        ).fetchall()
    assert json.loads(_decode_gzip_base64(archived_market_raw)) == {
        "ticker": {"price": 100, "quote_volume": 1, "change_pct_24h": 0},
        "book": {"bid": 99, "ask": 101, "spread_bps": 20},
    }
    assert json.loads(_decode_gzip_base64(archived_derivative_raw)) == {
        "premium": {"mark_price": 100, "index_price": 100, "funding_rate": 0},
        "open_interest": {"open_interest": 1},
    }
    assert json.loads(_decode_gzip_base64(archived_kline_raw)) == {
        "open_time": 1,
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
        "volume": 1,
        "quote_volume": 100,
        "close_time": 1,
    }
    assert json.loads(_decode_gzip_base64(archived_regime_payload)) == {"regime": "old"}
    assert remaining_klines == [("HOTUSDT", 0)]


def test_crypto_market_research_compacts_verbose_exchange_raw_on_save(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )

    service.repository.save_market_snapshot(
        symbol="BTCUSDT",
        market="spot",
        ticker={
            "symbol": "BTCUSDT",
            "price": 100,
            "quote_volume": 1_000,
            "change_pct_24h": 2.5,
            "raw": {
                "symbol": "BTCUSDT",
                "lastPrice": "100",
                "quoteVolume": "1000",
                "ignored_blob": "x" * 2000,
            },
        },
        book={
            "symbol": "BTCUSDT",
            "bid": 99,
            "ask": 101,
            "spread_bps": 20,
            "raw": {
                "symbol": "BTCUSDT",
                "bidPrice": "99",
                "bidQty": "1.5",
                "askPrice": "101",
                "askQty": "2.5",
                "ignored_blob": "y" * 2000,
            },
        },
        captured_at="2026-01-01T00:00:00+00:00",
    )
    service.repository.save_derivatives(
        symbol="BTCUSDT",
        premium={
            "symbol": "BTCUSDT",
            "mark_price": 100,
            "index_price": 99,
            "funding_rate": 0.01,
            "next_funding_time": 123,
            "raw": {
                "symbol": "BTCUSDT",
                "markPrice": "100",
                "ignored_blob": "z" * 2000,
            },
        },
        open_interest={
            "symbol": "BTCUSDT",
            "open_interest": 10,
            "error_message": "e" * 500,
            "raw": {
                "symbol": "BTCUSDT",
                "openInterest": "10",
                "ignored_blob": "w" * 2000,
            },
        },
        captured_at="2026-01-01T00:00:00+00:00",
    )

    with sqlite3.connect(db_path) as conn:
        market_raw = json.loads(
            conn.execute("SELECT raw_json FROM crypto_market_snapshots").fetchone()[0]
        )
        derivative_raw = json.loads(
            conn.execute("SELECT raw_json FROM crypto_derivatives").fetchone()[0]
        )

    assert market_raw["ticker"]["raw"]["_raw_compacted"] is True
    assert market_raw["ticker"]["raw"]["lastPrice"] == "100"
    assert "ignored_blob" not in market_raw["ticker"]["raw"]
    assert market_raw["book"]["raw"]["askQty"] == "2.5"
    assert "ignored_blob" not in market_raw["book"]["raw"]
    assert derivative_raw["premium"]["raw"]["markPrice"] == "100"
    assert "ignored_blob" not in derivative_raw["premium"]["raw"]
    assert derivative_raw["open_interest"]["raw"]["openInterest"] == "10"
    assert len(derivative_raw["open_interest"]["error_message"]) <= 180


def test_crypto_market_research_compacts_existing_verbose_raw_payloads(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO crypto_market_snapshots (
                symbol, market, price, quote_volume_usdt, change_pct_24h,
                spread_bps, raw_json, captured_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "spot",
                100,
                1000,
                1.0,
                2.0,
                json.dumps(
                    {
                        "ticker": {
                            "price": 100,
                            "raw": {"symbol": "BTCUSDT", "ignored_blob": "x" * 1000},
                        },
                        "book": {
                            "bid": 99,
                            "ask": 101,
                            "raw": {"symbol": "BTCUSDT", "ignored_blob": "y" * 1000},
                        },
                    }
                ),
                "2026-01-01T00:00:00+00:00",
            ),
        )

    result = service.repository.compact_verbose_raw_payloads(batch_size=10)

    assert result["updated"]["crypto_market_snapshots"] == 1
    assert result["remaining"]["crypto_market_snapshots"] == 0
    with sqlite3.connect(db_path) as conn:
        payload = json.loads(
            conn.execute("SELECT raw_json FROM crypto_market_snapshots").fetchone()[0]
        )
    assert payload["ticker"]["raw"]["_raw_compacted"] is True
    assert "ignored_blob" not in payload["ticker"]["raw"]


def test_crypto_market_research_prunes_archive_tables_when_enabled(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    old_at = "2020-01-01T00:00:00+00:00"
    service.repository.save_market_snapshot(
        symbol="BTCUSDT",
        market="spot",
        ticker={"price": 100, "quote_volume": 1, "change_pct_24h": 0},
        book={"bid": 99, "ask": 101, "spread_bps": 20},
        captured_at=old_at,
    )
    service.repository.save_klines(
        symbol="BTCUSDT",
        market="spot",
        interval="1m",
        rows=[
            {
                "open_time": 1,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
                "quote_volume": 100,
                "close_time": 1,
            }
        ],
    )
    service.repository.save_derivatives(
        symbol="BTCUSDT",
        premium={"mark_price": 100, "index_price": 100, "funding_rate": 0},
        open_interest={"open_interest": 1},
        captured_at=old_at,
    )
    service.repository.save_regime_snapshot({"regime": "old"})
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE crypto_regime_snapshots SET captured_at = ?", (old_at,))

    result = service.prune_history(
        market_retention_days=30,
        quant_retention_days=30,
        market_archive_retention_days=30,
    )

    assert result["market"]["archived"]["crypto_market_snapshots"] == 1
    assert result["market"]["archived"]["crypto_derivatives"] == 1
    assert result["market"]["archived"]["crypto_regime_snapshots"] == 1
    assert result["market"]["archived"]["crypto_klines"] == 1
    assert result["market"]["archive_deleted"]["crypto_market_snapshots_archive"] == 1
    assert result["market"]["archive_deleted"]["crypto_derivatives_archive"] == 1
    assert result["market"]["archive_deleted"]["crypto_regime_snapshots_archive"] == 1
    assert result["market"]["archive_deleted"]["crypto_klines_archive"] == 1
    assert result["market"]["archive_retention"]["vacuumed"] is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_market_snapshots_archive"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_derivatives_archive"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_regime_snapshots_archive"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM crypto_klines_archive"
        ).fetchone()[0] == 0


def test_crypto_market_research_forwards_quant_archive_window_rows(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeQuantRepository:
        def prune_history(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"status": "ok", "archive_window": {"deleted": 2}}

    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        quant_repository=FakeQuantRepository(),
    )

    result = service.prune_history(
        market_retention_days=7,
        quant_retention_days=3,
        quant_archive_retention_days=7,
        quant_hot_window_rows=120,
        quant_archive_window_rows=240,
    )

    assert result["quant"]["archive_window"]["deleted"] == 2
    assert calls == [
        {
            "retention_days": 3,
            "archive_retention_days": 7,
            "hot_window_rows": 120,
            "archive_window_rows": 240,
        }
    ]


def test_crypto_market_research_compacts_old_research_run_payloads(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    old_id = service.repository.save_research_run(
        status="ok",
        mode="llm",
        model="gpt-5.5",
        prompt={"symbol": "OLDUSDT", "blob": "x" * 512},
        response={"notes": ["old"], "blob": "y" * 256},
        error_message="",
    )
    recent_id = service.repository.save_research_run(
        status="ok",
        mode="llm",
        model="gpt-5.5",
        prompt={"symbol": "NEWUSDT", "blob": "z" * 512},
        response={"notes": ["new"], "blob": "w" * 256},
        error_message="",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE crypto_research_runs SET run_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00+00:00", old_id),
        )
        conn.execute(
            "UPDATE crypto_research_runs SET run_at = ? WHERE id = ?",
            ("2026-01-02T00:00:00+00:00", recent_id),
        )

    result = service.repository.prune_history(
        retention_days=30,
        research_run_recent_count=1,
        research_run_payload_min_chars=100,
    )

    assert result["compacted"]["crypto_research_runs"] == 1
    with sqlite3.connect(db_path) as conn:
        old_prompt, old_response = conn.execute(
            """
            SELECT prompt_json, response_json
            FROM crypto_research_runs
            WHERE id = ?
            """,
            (old_id,),
        ).fetchone()
        recent_prompt, recent_response = conn.execute(
            """
            SELECT prompt_json, response_json
            FROM crypto_research_runs
            WHERE id = ?
            """,
            (recent_id,),
        ).fetchone()

    old_prompt_payload = json.loads(old_prompt)
    old_response_payload = json.loads(old_response)
    assert old_prompt_payload["compacted"] is True
    assert old_prompt_payload["reason"] == "crypto_research_run_payload_retention"
    assert old_prompt_payload["field"] == "prompt_json"
    assert old_prompt_payload["original_chars"] > 100
    assert old_response_payload["compacted"] is True
    assert old_response_payload["field"] == "response_json"
    assert json.loads(recent_prompt)["blob"] == "z" * 512
    assert json.loads(recent_response)["blob"] == "w" * 256

    second = service.repository.prune_history(
        retention_days=30,
        research_run_recent_count=1,
        research_run_payload_min_chars=100,
    )
    assert second["compacted"]["crypto_research_runs"] == 0


def test_crypto_market_research_tiered_retention_deletes_cold_and_archives_warm(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path))
    )
    now = datetime.now(timezone.utc)
    warm_at = (now - timedelta(days=10)).isoformat()
    cold_at = (now - timedelta(days=20)).isoformat()
    warm_ms = int((now - timedelta(days=10)).timestamp() * 1000)
    cold_ms = int((now - timedelta(days=20)).timestamp() * 1000)

    for symbol, captured_at, close_time in (
        ("WARMUSDT", warm_at, warm_ms),
        ("COLDUSDT", cold_at, cold_ms),
    ):
        service.repository.save_market_snapshot(
            symbol=symbol,
            market="spot",
            ticker={"price": 100, "quote_volume": 1, "change_pct_24h": 0},
            book={"bid": 99, "ask": 101, "spread_bps": 20},
            captured_at=captured_at,
        )
        service.repository.save_klines(
            symbol=symbol,
            market="spot",
            interval="1m",
            rows=[
                {
                    "open_time": close_time - 60_000,
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1,
                    "quote_volume": 100,
                    "close_time": close_time,
                }
            ],
        )

    result = service.prune_history(
        market_retention_days=7,
        quant_retention_days=7,
        market_archive_retention_days=14,
    )

    assert result["market"]["cold_deleted"]["crypto_market_snapshots"] == 1
    assert result["market"]["cold_deleted"]["crypto_klines"] == 1
    assert result["market"]["archived"]["crypto_market_snapshots"] == 1
    assert result["market"]["archived"]["crypto_klines"] == 1
    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM crypto_market_snapshots").fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM crypto_klines").fetchone()[0] == 0
        archived_symbols = conn.execute(
            "SELECT symbol FROM crypto_market_snapshots_archive"
        ).fetchall()
        archived_klines = conn.execute(
            "SELECT symbol FROM crypto_klines_archive"
        ).fetchall()
    assert archived_symbols == [("WARMUSDT",)]
    assert archived_klines == [("WARMUSDT",)]


def test_crypto_market_research_prunes_hot_kline_windows_per_group(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(db_path), kline_hot_window_rows=2)
    )
    base_time = int(datetime.now(timezone.utc).timestamp() * 1000)

    for symbol in ("BTCUSDT", "ETHUSDT"):
        service.repository.save_klines(
            symbol=symbol,
            market="spot",
            interval="1m",
            rows=[
                {
                    "open_time": index,
                    "open": 100 + index,
                    "high": 101 + index,
                    "low": 99 + index,
                    "close": 100 + index,
                    "volume": 1,
                    "quote_volume": 100,
                    "close_time": base_time + index,
                }
                for index in range(5)
            ],
        )
    service.repository.save_klines(
        symbol="BTCUSDT",
        market="futures",
        interval="1m",
        rows=[
            {
                "open_time": index,
                "open": 200 + index,
                "high": 201 + index,
                "low": 199 + index,
                "close": 200 + index,
                "volume": 1,
                "quote_volume": 100,
                "close_time": base_time + index,
            }
            for index in range(4)
        ],
    )
    service.repository.save_klines(
        symbol="BTCUSDT",
        market="spot",
        interval="5m",
        rows=[
            {
                "open_time": index,
                "open": 300 + index,
                "high": 301 + index,
                "low": 299 + index,
                "close": 300 + index,
                "volume": 1,
                "quote_volume": 100,
                "close_time": base_time + index,
            }
            for index in range(3)
        ],
    )

    result = service.prune_history(market_retention_days=7, quant_retention_days=7)

    assert result["market"]["kline_window"]["deleted"] == 9
    with sqlite3.connect(db_path) as conn:
        remaining = conn.execute(
            """
            SELECT symbol, market, interval, open_time
            FROM crypto_klines
            ORDER BY symbol, market, interval, open_time
            """
        ).fetchall()

    assert remaining == [
        ("BTCUSDT", "futures", "1m", 2),
        ("BTCUSDT", "futures", "1m", 3),
        ("BTCUSDT", "spot", "1m", 3),
        ("BTCUSDT", "spot", "1m", 4),
        ("BTCUSDT", "spot", "5m", 1),
        ("BTCUSDT", "spot", "5m", 2),
        ("ETHUSDT", "spot", "1m", 3),
        ("ETHUSDT", "spot", "1m", 4),
    ]


def test_crypto_market_research_prunes_hot_snapshot_windows_per_group(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "crypto.db"
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(db_path),
            market_hot_window_rows=2,
        )
    )
    base_time = datetime.now(timezone.utc)

    for symbol in ("BTCUSDT", "ETHUSDT"):
        for index in range(5):
            service.repository.save_market_snapshot(
                symbol=symbol,
                market="spot",
                ticker={"price": 100 + index, "quote_volume": 1, "change_pct_24h": 0},
                book={"bid": 99, "ask": 101, "spread_bps": 20},
                captured_at=(base_time + timedelta(seconds=index)).isoformat(),
            )
    for index in range(4):
        service.repository.save_market_snapshot(
            symbol="BTCUSDT",
            market="futures",
            ticker={"price": 200 + index, "quote_volume": 1, "change_pct_24h": 0},
            book={"bid": 199, "ask": 201, "spread_bps": 20},
            captured_at=(base_time + timedelta(seconds=index)).isoformat(),
        )
        service.repository.save_derivatives(
            symbol="BTCUSDT",
            premium={"mark_price": 200 + index, "index_price": 200, "funding_rate": 0},
            open_interest={"open_interest": 1},
            captured_at=(base_time + timedelta(seconds=index)).isoformat(),
        )

    result = service.prune_history(market_retention_days=7, quant_retention_days=7)

    assert result["market"]["market_window"]["snapshots_deleted"] == 8
    assert result["market"]["market_window"]["derivatives_deleted"] == 2
    with sqlite3.connect(db_path) as conn:
        snapshots = conn.execute(
            """
            SELECT symbol, market, price
            FROM crypto_market_snapshots
            ORDER BY symbol, market, price
            """
        ).fetchall()
        derivatives = conn.execute(
            """
            SELECT symbol, mark_price
            FROM crypto_derivatives
            ORDER BY symbol, mark_price
            """
        ).fetchall()

    assert snapshots == [
        ("BTCUSDT", "futures", 202.0),
        ("BTCUSDT", "futures", 203.0),
        ("BTCUSDT", "spot", 103.0),
        ("BTCUSDT", "spot", 104.0),
        ("ETHUSDT", "spot", 103.0),
        ("ETHUSDT", "spot", 104.0),
    ]
    assert derivatives == [
        ("BTCUSDT", 202.0),
        ("BTCUSDT", 203.0),
    ]


def test_squeeze_risk_scores_crowded_short(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )
    feature = service._squeeze_risk_feature(
        {
            "funding_rate": -0.0012,
            "mark_index_basis_pct": -0.25,
            "open_interest": 2_000_000,
            "trend_1m": "down",
        }
    )

    assert feature["squeeze_risk"] in {"short_squeeze", "high_short_squeeze"}
    assert feature["squeeze_risk_score"] >= 70
    assert "negative funding" in " ".join(feature["squeeze_risk_reasons"])


def test_entry_quality_penalizes_chasing_extended_move(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    quality = service._entry_quality_feature(
        {
            "timeframes": {
                "1m": {"momentum_pct": 4.0, "trend": "up"},
                "5m": {"momentum_pct": 8.0, "trend": "up"},
                "15m": {"momentum_pct": 12.0, "trend": "up"},
            },
            "spread_bps": 1.0,
            "squeeze_risk_score": 20.0,
        }
    )

    assert quality["entry_quality"] == "wait_pullback"
    assert quality["entry_quality_score"] < 60


def test_resolve_universe_merges_static_and_dynamic_liquid_symbols(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto.db"),
            max_symbols=5,
            auto_universe_limit=4,
            min_quote_volume_usdt=100_000,
        ),
        binance=_FakeUniverseBinance(),
    )

    result = asyncio.run(service.resolve_universe(["BTCUSDT", "ETHUSDT"]))

    assert result["symbols"] == ["BTCUSDT", "ETHUSDT", "PEPEUSDT", "SOLUSDT", "DOGEUSDT"]
    assert result["static_count"] == 2
    assert result["dynamic_count"] == 3
    assert "USDCUSDT" in result["excluded_symbols"]
    assert "BTCUPUSDT" in result["excluded_symbols"]


def test_resolve_universe_can_observe_top_three_hundred_dynamic_symbols(
    tmp_path: Path,
) -> None:
    class WideUniverseBinance(_FakeBinance):
        async def fetch_24h_tickers(self, *, market: str = "spot") -> list[dict[str, Any]]:
            assert market == "spot"
            return [
                {
                    "symbol": f"ALT{idx:03d}USDT",
                    "quote_volume": 1_000_000_000 - idx,
                }
                for idx in range(320)
            ]

    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto.db"),
            max_symbols=300,
            auto_universe_limit=300,
            min_quote_volume_usdt=100_000,
        ),
        binance=WideUniverseBinance(),
    )

    result = asyncio.run(service.resolve_universe([]))

    assert len(result["symbols"]) == 300
    assert result["symbols"][0] == "ALT000USDT"
    assert result["symbols"][-1] == "ALT299USDT"
    assert result["dynamic_count"] == 300
    assert service.repository.observed_symbol_count() == 300

    context = service.latest_context(limit=80)

    assert context["observed_symbol_count"] == 300
    assert context["observe_universe_count"] == 300


def test_spark_research_focuses_on_top_ranked_symbols(tmp_path: Path) -> None:
    spark = _FakeSpark()
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(
            db_path=str(tmp_path / "crypto.db"),
            max_symbols=10,
            llm_top_symbols=2,
        ),
        codex_runtime=spark,
    )
    for symbol, score, volume in [
        ("LOWUSDT", 20, 10_000_000),
        ("MIDUSDT", 60, 20_000_000),
        ("TOPUSDT", 90, 30_000_000),
    ]:
        service.repository.upsert_features(
            symbol,
            {
                "symbol": symbol,
                "trend_1m": "up",
                "quote_volume_usdt": volume,
                "score": score,
            },
        )

    result = asyncio.run(
        service.run_research_once(symbols=["LOWUSDT", "MIDUSDT", "TOPUSDT"])
    )
    prompt_inputs = spark.calls[0]["prompt"]["inputs"]

    assert result["status"] == "ok"
    assert prompt_inputs["symbols"] == ["TOPUSDT", "MIDUSDT"]
    assert prompt_inputs["observed_symbol_count"] == 3


def test_crypto_research_prompt_contains_jue_workflow(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        codex_runtime=_FakeSpark(),
    )

    prompt = service._build_research_prompt(
        {"symbols": ["BTCUSDT"], "observed_symbol_count": 1}
    )
    workflow = prompt["jue_workflow"]
    skill_ids = {row["skill_id"] for row in workflow["skills"]}

    assert workflow["workflow_id"] == "crypto_research"
    assert (
        workflow["model_policy"]["expected_runtime_model"]
        == "gpt-5.5"
    )
    assert {"crypto_market_sweep", "evidence_audit"}.issubset(skill_ids)
    assert prompt["language_policy"]["internal_reasoning_language"] == "en-US"
    assert prompt["language_policy"]["operator_display_language"] == "ko-KR"
    assert prompt["language_policy"]["user_visible_generation_order"] == (
        "draft_conclusion_in_english_then_translate_to_korean_for_display"
    )
    assert workflow["language_policy"] == prompt["language_policy"]


def test_collect_keeps_spot_features_when_futures_data_is_unavailable(
    tmp_path: Path,
) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        binance=_FuturesUnavailableBinance(),
    )

    result = asyncio.run(service.collect_market_structure(["BTCUSDT"]))
    context = service.latest_context(limit=5)

    assert result["status"] == "ok"
    assert result["collected_count"] == 1
    features = context["items"][0]["features"]
    assert features["trend_1m"] == "up"
    assert features["derivatives_status"] == "partial"
    assert features["funding_rate"] == pytest.approx(0.0)


def test_external_context_is_normalized_and_limited(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db"))
    )

    result = service.save_external_context(
        source_id="coingecko",
        key="BTC",
        payload={
            "market_cap_rank": 1,
            "developer_score": 92,
            "community_score": 81,
            "description": "A" * 5000,
        },
    )
    context = service.external_context(keys=["BTC"], limit=3)

    assert result["status"] == "ok"
    assert context["items"][0]["source_id"] == "coingecko"
    assert context["items"][0]["key"] == "BTC"
    assert len(context["items"][0]["payload"]["description"]) <= 1200


def test_run_spark_research_persists_notes_and_candidates(tmp_path: Path) -> None:
    spark = _FakeSpark()
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        codex_runtime=spark,
    )
    service.repository.upsert_features(
        "BTCUSDT",
        {"symbol": "BTCUSDT", "trend_1m": "up", "quote_volume_usdt": 2_000_000},
    )

    result = asyncio.run(service.run_research_once(symbols=["BTCUSDT"]))
    context = service.latest_context(limit=5)

    assert result["status"] == "ok"
    assert result["candidate_count"] == 1
    assert context["candidates"][0]["symbol"] == "BTCUSDT"
    assert context["symbol_notes"]["BTCUSDT"]["stance"] == "long_watch"
    assert spark.calls[0]["kwargs"] == {
        "model": "gpt-5.5",
        "reasoning_effort": "xhigh",
    }


def test_run_spark_research_records_llm_failure_as_error(tmp_path: Path) -> None:
    service = CryptoMarketResearchService(
        config=CryptoMarketResearchConfig(db_path=str(tmp_path / "crypto.db")),
        codex_runtime=_FailingSpark(),
    )
    service.repository.upsert_features(
        "BTCUSDT",
        {"symbol": "BTCUSDT", "trend_1m": "up", "quote_volume_usdt": 2_000_000},
    )

    result = asyncio.run(service.run_research_once(symbols=["BTCUSDT"]))

    assert result["status"] == "error"
    assert result["candidate_count"] == 0
    assert "spark unavailable" in result["error_message"]
