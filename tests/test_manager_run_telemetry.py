from __future__ import annotations

from tradecraft.services.manager_run_telemetry import (
    ManagerRunTelemetryV1,
    build_fill_provenance_summary,
    build_strategy_authority_gate,
)


def test_fill_provenance_keeps_adoption_and_rejections_out_of_alpha() -> None:
    summary = build_fill_provenance_summary(
        actions={
            "create_blocks": [
                {"symbol": "BTCUSDT", "execution_source": "exchange_fill"},
                {"symbol": "ETHUSDT", "execution_source": "paper_fill"},
            ],
            "adopt_existing_blocks": [{"symbol": "005930"}],
            "adopt_wallet_blocks": [{"symbol": "SOLUSDT"}],
            "rejected_create_blocks": [{"symbol": "XRPUSDT"}],
        },
        applied={
            "created": [
                {"symbol": "BTCUSDT", "fill_provenance": "exchange_fill"},
                {"symbol": "ETHUSDT", "fill_provenance": "paper_fill"},
            ]
        },
    )

    assert summary["jue_exchange_fill_count"] == 1
    assert summary["paper_fill_count"] == 1
    assert summary["kis_existing_position_adoption_count"] == 1
    assert summary["binance_wallet_adoption_count"] == 1
    assert summary["failed_or_rejected_entry_count"] == 1
    assert summary["alpha_fill_count"] == 1


def test_manager_run_telemetry_serializes_cost_and_latency_contract() -> None:
    telemetry = ManagerRunTelemetryV1(
        venue="binance",
        context_generation_ms=120.5,
        prompt_chars=42_000,
        llm_latency_ms=850.2,
        raw_prompt_chars=84_000,
        input_tokens=10_000,
        output_tokens=800,
        action_count=2,
        result_status="ok",
        fill_provenance={"alpha_fill_count": 1},
    )

    payload = telemetry.to_dict()

    assert payload["version"] == "manager_run_telemetry_v1"
    assert payload["context_generation_ms"] == 120.5
    assert payload["prompt_chars"] == 42_000
    assert payload["prompt_reduction_pct"] == 50.0
    assert payload["fill_provenance"]["alpha_fill_count"] == 1


def test_manager_run_telemetry_adds_optional_wiki_fields_compatibly() -> None:
    legacy = ManagerRunTelemetryV1(
        venue="kis",
        context_generation_ms=1.0,
        prompt_chars=100,
        llm_latency_ms=2.0,
    ).to_dict()
    enriched = ManagerRunTelemetryV1(
        venue="kis",
        context_generation_ms=1.0,
        prompt_chars=100,
        llm_latency_ms=2.0,
        wiki_read_mode="required",
        wiki_snapshot_id="snapshot:kis:1",
        wiki_coverage_status="sufficient",
        wiki_context_chars=321,
        wiki_shadow_comparison_id="shadow:1",
        wiki_shadow_recording_id="recording:1",
        wiki_suppressed_new_risk_count=2,
    ).to_dict()

    assert set(legacy) <= set(enriched)
    assert legacy["wiki_read_mode"] == "shadow"
    assert enriched["wiki_snapshot_id"] == "snapshot:kis:1"
    assert enriched["wiki_shadow_recording_id"] == "recording:1"
    assert enriched["wiki_suppressed_new_risk_count"] == 2


def test_strategy_authority_stays_restricted_without_fill_proven_sample() -> None:
    observe = build_strategy_authority_gate(
        fill_proven_sample_count=0,
        attribution_complete=False,
        net_return_after_cost_pct=10.0,
    )
    restricted = build_strategy_authority_gate(
        fill_proven_sample_count=12,
        attribution_complete=True,
        net_return_after_cost_pct=2.0,
    )
    eligible = build_strategy_authority_gate(
        fill_proven_sample_count=40,
        attribution_complete=True,
        net_return_after_cost_pct=2.0,
    )

    assert observe["authority"] == "observe_only"
    assert restricted["authority"] == "restricted"
    assert eligible["authority"] == "eligible_for_review"
