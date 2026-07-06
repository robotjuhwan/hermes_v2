from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from tradecraft.services.llm_usage import LLMUsageRepository, estimate_tokens


def test_estimate_tokens_is_stable_for_korean_and_json_text() -> None:
    assert estimate_tokens("삼성전자 목표가와 손절가를 검토한다.") >= 6
    assert estimate_tokens('{"symbol":"005930","qty":2}') >= 6


def test_record_call_and_daily_summary(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    repo.record_call(
        component="kis_block_manager",
        operation="run_manager_once",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=1234,
        prompt_tokens=100,
        completion_tokens=25,
        total_tokens=125,
        usage_source="exact",
        input_chars=800,
        output_chars=200,
        metadata={"block_count": 3},
        started_at="2026-05-13T00:00:00+00:00",
        finished_at="2026-05-13T00:00:01+00:00",
    )

    summary = repo.daily_summary("2026-05-13")

    assert summary["trading_day"] == "2026-05-13"
    assert summary["total"]["call_count"] == 1
    assert summary["total"]["total_tokens"] == 125
    assert summary["total"]["exact_token_count"] == 1
    assert summary["total"]["missing_token_count"] == 0
    assert summary["total"]["input_chars"] == 800
    assert summary["total"]["avg_input_chars"] == 800
    assert summary["total"]["max_input_chars"] == 800
    assert summary["by_component"][0]["component"] == "kis_block_manager"
    assert summary["by_component"][0]["label"] == "KIS 쥬 판단"
    assert "국장" in summary["by_component"][0]["description"]
    assert summary["by_component"][0]["total_tokens"] == 125
    assert summary["by_component"][0]["max_input_chars"] == 800
    assert summary["by_model"][0]["model"] == "gpt-5.5"
    assert summary["by_model"][0]["total_tokens"] == 125
    assert summary["by_status"][0]["status"] == "ok"
    assert summary["by_status"][0]["call_count"] == 1
    assert summary["by_usage_source"][0]["usage_source"] == "exact"
    assert summary["by_usage_source"][0]["call_count"] == 1


def test_daily_summary_rolls_up_model_status_and_usage_source(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    repo.record_call(
        component="kis_block_manager",
        operation="run_manager_once",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=100,
        prompt_tokens=80,
        completion_tokens=20,
        total_tokens=100,
        usage_source="exact",
        input_chars=320,
        output_chars=80,
        started_at="2026-05-13T00:00:00+00:00",
        finished_at="2026-05-13T00:00:01+00:00",
    )
    repo.record_call(
        component="investment_memory",
        operation="reflect",
        model="gpt-5.5-mini",
        mode="sdk",
        status="error",
        latency_ms=50,
        prompt_tokens=10,
        completion_tokens=0,
        total_tokens=10,
        usage_source="estimated",
        input_chars=40,
        output_chars=0,
        started_at="2026-05-13T01:00:00+00:00",
        finished_at="2026-05-13T01:00:01+00:00",
    )
    repo.record_call(
        component="research_ask",
        operation="",
        model="gpt-5.5",
        mode="none",
        status="error",
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        usage_source="missing",
        input_chars=0,
        output_chars=0,
        started_at="2026-05-13T02:00:00+00:00",
        finished_at="2026-05-13T02:00:01+00:00",
    )

    summary = repo.daily_summary("2026-05-13")

    assert summary["total"]["call_count"] == 3
    assert summary["total"]["ok_count"] == 1
    assert summary["total"]["error_count"] == 2
    assert summary["total"]["exact_token_count"] == 1
    assert summary["total"]["estimated_token_count"] == 1
    assert summary["total"]["missing_token_count"] == 1

    by_model = {row["model"]: row for row in summary["by_model"]}
    assert by_model["gpt-5.5"]["call_count"] == 2
    assert by_model["gpt-5.5"]["missing_token_count"] == 1
    assert by_model["gpt-5.5-mini"]["total_tokens"] == 10

    by_status = {row["status"]: row for row in summary["by_status"]}
    assert by_status["ok"]["call_count"] == 1
    assert by_status["error"]["call_count"] == 2
    assert by_status["error"]["missing_token_count"] == 1

    by_usage_source = {
        row["usage_source"]: row for row in summary["by_usage_source"]
    }
    assert by_usage_source["exact"]["total_tokens"] == 100
    assert by_usage_source["estimated"]["call_count"] == 1
    assert by_usage_source["missing"]["missing_token_count"] == 1


def test_period_summary_rolls_up_multiple_days_and_keeps_component_labels(
    tmp_path,
) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    repo.record_call(
        component="kis_block_manager",
        operation="manager_run",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=100,
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        usage_source="estimated",
        input_chars=400,
        output_chars=80,
        started_at="2026-06-17T00:00:00+00:00",
        finished_at="2026-06-17T00:00:01+00:00",
    )
    repo.record_call(
        component="market_judge",
        operation="judge",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=200,
        prompt_tokens=50,
        completion_tokens=10,
        total_tokens=60,
        usage_source="estimated",
        input_chars=200,
        output_chars=40,
        started_at="2026-06-18T00:00:00+00:00",
        finished_at="2026-06-18T00:00:01+00:00",
    )

    summary = repo.period_summary(
        period="7d",
        start_day="2026-06-17",
        end_day="2026-06-23",
    )

    assert summary["period"] == "7d"
    assert summary["start_day"] == "2026-06-17"
    assert summary["end_day"] == "2026-06-23"
    assert summary["total"]["call_count"] == 2
    assert summary["total"]["total_tokens"] == 180
    by_component = {row["component"]: row for row in summary["by_component"]}
    assert by_component["kis_block_manager"]["label"] == "KIS 쥬 판단"
    assert by_component["market_judge"]["label"] == "국장 장중 판단"


def test_all_period_summary_uses_full_llm_usage_history(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    repo.record_call(
        component="binance_block_manager",
        operation="manager_run",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=100,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        usage_source="estimated",
        input_chars=40,
        output_chars=20,
        started_at="2026-05-01T00:00:00+00:00",
        finished_at="2026-05-01T00:00:01+00:00",
    )
    repo.record_call(
        component="research_reports",
        operation="fact_extract",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=100,
        prompt_tokens=20,
        completion_tokens=5,
        total_tokens=25,
        usage_source="estimated",
        input_chars=80,
        output_chars=20,
        started_at="2026-06-20T00:00:00+00:00",
        finished_at="2026-06-20T00:00:01+00:00",
    )

    summary = repo.period_summary(period="all")

    assert summary["period"] == "all"
    assert summary["start_day"] == "2026-05-01"
    assert summary["end_day"] == "2026-06-20"
    assert summary["total"]["call_count"] == 2
    assert summary["total"]["total_tokens"] == 40
    assert {row["label"] for row in summary["by_component"]} == {
        "바이낸스 쥬 판단",
        "네이버 리포트 지식화",
    }


def test_retired_kis_legacy_trader_usage_is_labeled_as_a_record(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    repo.record_call(
        component="kis_legacy_trader",
        operation="run_once",
        model="gpt-5.5",
        mode="sdk",
        status="error",
        latency_ms=100,
        prompt_tokens=10,
        completion_tokens=0,
        total_tokens=10,
        usage_source="estimated",
        input_chars=40,
        output_chars=0,
        started_at="2026-06-14T00:00:00+00:00",
        finished_at="2026-06-14T00:00:01+00:00",
    )

    summary = repo.daily_summary("2026-06-14")
    row = summary["by_component"][0]

    assert row["component"] == "kis_legacy_trader"
    assert row["label"] == "퇴역 KIS 직접 트레이더 기록"
    assert row["category"] == "retired"
    assert "현재 주문 판단" in row["description"]


def test_repository_serializes_non_json_metadata_values(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    row = repo.record_call(
        component="research_ask",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=1,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        usage_source="estimated",
        input_chars=4,
        output_chars=4,
        metadata={
            "started": datetime(2026, 5, 13, tzinfo=timezone.utc),
            "path": Path("/tmp/prompt.json"),
            "ratio": Decimal("1.25"),
            "bytes": b"abc",
        },
        started_at="2026-05-13T00:00:00+00:00",
        finished_at="2026-05-13T00:00:01+00:00",
    )

    metadata = json.loads(str(row["metadata_json"]))
    assert metadata["started"] == "2026-05-13 00:00:00+00:00"
    assert metadata["path"] == "/tmp/prompt.json"
    assert metadata["ratio"] == "1.25"
    assert metadata["bytes"] == "b'abc'"


def test_repository_connection_uses_sqlite_write_pragmas(tmp_path) -> None:
    repo = LLMUsageRepository(str(tmp_path / "llm_usage.db"))

    with repo._connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= 5000
    assert int(synchronous) == 1
