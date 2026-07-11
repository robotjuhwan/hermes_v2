from __future__ import annotations

import importlib
from pathlib import Path

from tradecraft.services.llm_usage import LLMUsageRepository


def test_llm_usage_recovery_enrichment_lives_in_api_payload_module(tmp_path) -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")
    db_path = tmp_path / "llm_usage.db"
    repository = LLMUsageRepository(str(db_path))
    repository.record_call(
        component="market_judge",
        operation="judge",
        model="gpt-5.5",
        mode="sdk",
        status="error",
        latency_ms=1200,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        usage_source="estimated",
        input_chars=250_000,
        output_chars=0,
        started_at="2026-06-20T09:00:00+09:00",
        finished_at="2026-06-20T09:00:02+09:00",
    )
    repository.record_call(
        component="market_judge",
        operation="judge",
        model="gpt-5.5",
        mode="sdk",
        status="ok",
        latency_ms=900,
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        usage_source="exact",
        input_chars=120_000,
        output_chars=500,
        started_at="2026-06-20T09:30:00+09:00",
        finished_at="2026-06-20T09:30:02+09:00",
    )

    summary = {
        "trading_day": "2026-06-20",
        "by_component": [
            {
                "component": "market_judge",
                "call_count": 2,
                "error_count": 1,
            }
        ],
    }

    enriched = module.enrich_llm_usage_component_recovery(summary, str(db_path))
    row = enriched["by_component"][0]

    assert row["latest_status"] == "ok"
    assert row["latest_input_chars"] == 120_000
    assert row["latest_error_at"] == "2026-06-20T09:00:00+09:00"
    assert row["ok_after_latest_error_count"] == 1


def test_llm_usage_recovery_enrichment_surfaces_db_failure(tmp_path) -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")

    summary = {
        "trading_day": "2026-06-20",
        "by_component": [{"component": "kis_block_manager"}],
    }

    enriched = module.enrich_llm_usage_component_recovery(summary, str(tmp_path))

    assert enriched["recovery_enrichment_status"] == "error"
    assert "recovery_enrichment_error" in enriched
    assert enriched["by_component"] == [{"component": "kis_block_manager"}]


def test_llm_usage_status_payload_builder_is_decoupled_from_main() -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")

    payload = module.build_llm_usage_status_payload(
        enabled=True,
        db_path=".runtime/llm_usage.db",
        summary={
            "period": "today",
            "trading_day": "2026-06-20",
            "total": {"call_count": 3, "total_tokens": 1200},
            "by_component": [{"component": "kis_block_manager", "call_count": 1}],
        },
    )

    assert payload == {
        "status": "ok",
        "enabled": True,
        "db_path": ".runtime/llm_usage.db",
        "period": "today",
        "trading_day": "2026-06-20",
        "total": {"call_count": 3, "total_tokens": 1200},
        "by_component": [{"component": "kis_block_manager", "call_count": 1}],
        "components": [{"component": "kis_block_manager", "call_count": 1}],
        "today": {
            "period": "today",
            "trading_day": "2026-06-20",
            "total": {"call_count": 3, "total_tokens": 1200},
            "by_component": [{"component": "kis_block_manager", "call_count": 1}],
        },
    }


def test_llm_usage_semantic_check_lives_in_api_payload_module() -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")

    payload = {
        "today": {
            "by_component": [
                {
                    "component": "binance_block_manager",
                    "call_count": 5,
                    "error_count": 0,
                    "max_input_chars": 260_000,
                    "avg_input_chars": 120_000,
                    "latest_input_chars": 260_000,
                    "latest_status": "error",
                    "latest_started_at": "2026-06-20T00:00:00+00:00",
                },
                {
                    "component": "investment_memory",
                    "call_count": 5,
                    "error_count": 3,
                    "latest_status": "error",
                    "ok_after_latest_error_count": 0,
                    "latest_error_at": "2026-06-20T00:05:00+00:00",
                },
                {
                    "component": "kis_legacy_trader",
                    "call_count": 5,
                    "error_count": 5,
                    "latest_status": "error",
                    "ok_after_latest_error_count": 0,
                },
                {
                    "component": "market_judge",
                    "call_count": 5,
                    "error_count": 3,
                    "latest_status": "ok",
                    "ok_after_latest_error_count": 3,
                    "latest_error_at": "2026-06-20T00:10:00+00:00",
                },
            ]
        }
    }

    result = module.build_llm_usage_semantic_check(
        payload,
        processes={
            "binance_block_trader": {
                "started_at_epoch": 1_781_913_605,
            }
        },
        component_enabled={
            "investment_memory": True,
            "market_judge": True,
            "binance_block_manager": True,
        },
    )

    assert result["warnings"] == ["llm_error_rate_high"]
    check = result["check"]
    assert check["stale_prompt_large_components"][0]["component"] == "binance_block_manager"
    assert check["error_rate_components"][0]["component"] == "investment_memory"
    assert check["inactive_error_components"][0]["component"] == "kis_legacy_trader"
    assert check["recovered_error_components"][0]["component"] == "market_judge"


def test_llm_usage_semantic_check_treats_retired_components_as_inactive_by_default() -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")

    payload = {
        "today": {
            "by_component": [
                {
                    "component": "kis_legacy_trader",
                    "call_count": 5,
                    "error_count": 5,
                    "latest_status": "error",
                    "ok_after_latest_error_count": 0,
                },
            ]
        }
    }

    result = module.build_llm_usage_semantic_check(payload)

    assert result["warnings"] == []
    assert result["check"]["inactive_error_components"] == [
        {
            "component": "kis_legacy_trader",
            "call_count": 5,
            "error_count": 5,
            "error_rate": 1.0,
        }
    ]


def test_llm_usage_semantic_check_treats_error_before_restart_as_stale() -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")

    payload = {
        "today": {
            "by_component": [
                {
                    "component": "symbol_analysis",
                    "call_count": 10,
                    "error_count": 4,
                    "latest_status": "error",
                    "latest_started_at": "2026-06-20T00:05:00+00:00",
                    "latest_error_at": "2026-06-20T00:05:00+00:00",
                    "ok_after_latest_error_count": 0,
                },
            ]
        }
    }

    result = module.build_llm_usage_semantic_check(
        payload,
        processes={
            "kis_block_trader": {
                "started_at_epoch": 1_782_432_600,
            }
        },
        component_enabled={"symbol_analysis": True},
    )

    assert "llm_error_rate_high" not in result["warnings"]
    stale = result["check"]["stale_error_components"]
    assert stale == [
        {
            "component": "symbol_analysis",
            "call_count": 10,
            "error_count": 4,
            "error_rate": 0.4,
        }
    ]


def test_llm_usage_semantic_check_treats_successful_under_cap_prompt_as_observation() -> None:
    module = importlib.import_module("tradecraft.api.llm_payloads")

    payload = {
        "today": {
            "by_component": [
                {
                    "component": "kis_block_manager",
                    "call_count": 1,
                    "error_count": 0,
                    "max_input_chars": 186_669,
                    "avg_input_chars": 186_669,
                    "latest_input_chars": 186_669,
                    "latest_status": "ok",
                    "latest_started_at": "2026-06-28T23:30:17+00:00",
                }
            ]
        }
    }

    result = module.build_llm_usage_semantic_check(payload)

    assert "llm_prompt_payload_large" not in result["warnings"]
    assert result["check"]["prompt_large_components"] == []
    assert result["check"]["prompt_near_limit_components"][0]["component"] == (
        "kis_block_manager"
    )


def test_large_prompt_warning_requires_three_under_warn_successes_to_clear() -> None:
    def payload(successes: int) -> dict:
        return {
            "today": {
                "by_component": [
                    {
                        "component": "market_judge",
                        "call_count": 5,
                        "error_count": 0,
                        "max_input_chars": 355_000,
                        "avg_input_chars": 220_000,
                        "latest_input_chars": 120_000,
                        "latest_status": "ok",
                        "latest_large_prompt_at": "2026-07-10T00:00:00+00:00",
                        "ok_under_warn_after_large_count": successes,
                    }
                ]
            }
        }

    module = importlib.import_module("tradecraft.api.llm_payloads")
    warning = module.build_llm_usage_semantic_check(payload(2))
    recovered = module.build_llm_usage_semantic_check(payload(3))

    assert "llm_prompt_payload_large" in warning["warnings"]
    assert "llm_prompt_payload_large" not in recovered["warnings"]
    assert recovered["check"]["recovered_prompt_large_components"][0][
        "ok_under_warn_after_large_count"
    ] == 3


def test_main_no_longer_owns_low_level_llm_usage_semantic_helpers() -> None:
    source = Path("src/tradecraft/main.py").read_text(encoding="utf-8")

    assert "def _llm_usage_component_active" not in source
    assert "def _llm_usage_recovery_success_threshold" not in source
    assert "def _llm_usage_component_process_key" not in source
    assert "def _llm_usage_stale_after_process_restart" not in source
