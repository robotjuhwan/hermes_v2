from __future__ import annotations

from tradecraft.services.binance_manager_actions import (
    empty_manager_action_results,
    manager_close_has_adverse_evidence,
    manager_closed_fields,
    manager_exit_request_fields,
    manager_growth_governor_create_rejection,
    manager_market_horizon_conflict,
    manager_block_action_result,
    manager_created_block_result,
    manager_create_block_metadata,
    manager_create_policy_repair_rejection,
    manager_pause_fields,
    manager_update_fields,
    rejected_manager_action,
    validation_repair_metadata_update,
)


def test_empty_manager_action_results_has_stable_buckets() -> None:
    assert empty_manager_action_results() == {
        "adopted": [],
        "created": [],
        "updated": [],
        "closed": [],
        "paused": [],
    }


def test_rejected_manager_action_keeps_reason_input_and_non_empty_context() -> None:
    row = {"symbol": "BTCUSDT"}

    assert rejected_manager_action(
        "entry_gate_rejected:weak_evidence",
        input_row=row,
        gate={"ok": False},
        ignored={},
        message="",
    ) == {
        "status": "rejected",
        "reason": "entry_gate_rejected:weak_evidence",
        "gate": {"ok": False},
        "input": row,
    }


def test_manager_created_block_result_distinguishes_waiting_created_and_live_entry() -> None:
    block = {"block_id": "bnb_futures_BTCUSDT_20260601010101000000"}

    assert manager_created_block_result(
        block,
        live_entry=False,
        waiting_entry=True,
    ) == {
        "status": "waiting_entry",
        "block_id": "bnb_futures_BTCUSDT_20260601010101000000",
    }
    assert manager_created_block_result(
        block,
        live_entry=False,
        waiting_entry=False,
    ) == {
        "status": "created",
        "block_id": "bnb_futures_BTCUSDT_20260601010101000000",
    }
    assert manager_created_block_result(
        block,
        live_entry=True,
        waiting_entry=False,
        entry={"status": "submitted", "order": {"id": "order-1"}},
    ) == {
        "status": "submitted",
        "block_id": "bnb_futures_BTCUSDT_20260601010101000000",
        "order": {"id": "order-1"},
    }


def test_manager_create_block_metadata_preserves_gates_and_policy_audit() -> None:
    metadata = manager_create_block_metadata(
        {"metadata": {"existing": True}},
        entry_gate={"ok": True, "score": 0.72},
        live_authority_gate={"ok": True, "mode": "live"},
        lane_authority_gate={"ok": True, "lane": "futures:long:short"},
        cost_edge_gate={"ok": True, "edge": 1.8},
        growth_governor={"mode": "edge_rebuild"},
        growth_governor_applies=True,
        policy_impacts=[
            {
                "rule_id": "reduce_crypto_probe_after_cost_drag@v1",
                "effect": {
                    "risk_note": "reduce after cost drag",
                    "target_stop_review": True,
                },
            }
        ],
        policy_enforcement={
            "adjustments": [{"field": "qty", "before": 1.0, "after": 0.5}],
            "checks": [{"field": "entry_style", "ok": True}],
        },
    )

    assert metadata["existing"] is True
    assert metadata["entry_gate"] == {"ok": True, "score": 0.72}
    assert metadata["live_authority_gate"] == {"ok": True, "mode": "live"}
    assert metadata["lane_authority_gate"] == {
        "ok": True,
        "lane": "futures:long:short",
    }
    assert metadata["cost_edge_gate"] == {"ok": True, "edge": 1.8}
    assert metadata["growth_governor"] == {"mode": "edge_rebuild"}
    assert metadata["policy_rule_impacts"][0]["rule_id"] == (
        "reduce_crypto_probe_after_cost_drag@v1"
    )
    assert metadata["applied_policy_versions"] == [
        "reduce_crypto_probe_after_cost_drag@v1"
    ]
    assert metadata["policy_effect_audit"]["version"] == "policy_effect_audit_v1"
    assert metadata["policy_effect_enforcement"]["adjustments"][0]["after"] == 0.5


def test_manager_create_block_metadata_preserves_top_level_wiki_repair_pressure() -> None:
    metadata = manager_create_block_metadata(
        {
            "jue_wiki_repair_pressure": "repair queue omitted 4 ETHUSDT items",
            "jue_wiki_repair_resolution": "reduced size until narrative refresh",
            "period_memory_coverage_gap": "binance weekly replay missing",
            "period_memory_override_reason": (
                "current live evidence overrides the replay gap"
            ),
            "metadata_contract_audit_resolution": (
                "contract was complete after explicit override reason"
            ),
        },
        entry_gate={},
        live_authority_gate={},
        lane_authority_gate={},
        cost_edge_gate={},
        growth_governor={},
        growth_governor_applies=False,
        policy_impacts=[],
        policy_enforcement={},
    )

    assert metadata["jue_wiki_repair_pressure"] == (
        "repair queue omitted 4 ETHUSDT items"
    )
    assert metadata["jue_wiki_repair_resolution"] == (
        "reduced size until narrative refresh"
    )
    assert metadata["period_memory_coverage_gap"] == "binance weekly replay missing"
    assert metadata["period_memory_override_reason"] == (
        "current live evidence overrides the replay gap"
    )
    assert metadata["metadata_contract_audit_resolution"] == (
        "contract was complete after explicit override reason"
    )


def test_manager_create_block_metadata_marks_period_memory_contract_gap() -> None:
    metadata = manager_create_block_metadata(
        {
            "period_memory_coverage_gap": "binance weekly replay missing",
            "metadata_contract_repair_note": (
                "metadata contract repair: "
                "add_period_memory_override_reason_before_scaling; "
                "resolution: kept micro probe until override reason is restored"
            ),
            "metadata_contract_audit_resolution": (
                "kept micro probe until override reason is restored"
            ),
        },
        entry_gate={},
        live_authority_gate={},
        lane_authority_gate={},
        cost_edge_gate={},
        growth_governor={},
        growth_governor_applies=False,
        policy_impacts=[],
        policy_enforcement={},
    )

    assert metadata["period_memory_contract_audit"] == {
        "status": "missing_override_reason",
        "policy_id": "period_memory_coverage.missing_override_reason",
        "gap": "binance weekly replay missing",
        "override_reason": "",
        "metadata_contract_audit_resolution": (
            "kept micro probe until override reason is restored"
        ),
        "metadata_contract_repair_note": (
            "metadata contract repair: "
            "add_period_memory_override_reason_before_scaling; "
            "resolution: kept micro probe until override reason is restored"
        ),
        "missing_metadata": ["period_memory_override_reason"],
        "required_metadata": [
            "period_memory_coverage_gap",
            "period_memory_override_reason",
        ],
        "repair_action": "add_period_memory_override_reason_before_scaling",
    }
    assert metadata["metadata_contract_audit_resolution"] == (
        "kept micro probe until override reason is restored"
    )
    assert metadata["metadata_contract_repair_note"] == (
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept micro probe until override reason is restored"
    )


def test_manager_block_action_result_keeps_block_id_and_context() -> None:
    assert manager_block_action_result(
        "exit_requested",
        "bnb_futures_ETHUSDT_20260601010101000000",
        reason="manager_close_requested",
    ) == {
        "status": "exit_requested",
        "block_id": "bnb_futures_ETHUSDT_20260601010101000000",
        "reason": "manager_close_requested",
    }


def test_manager_market_horizon_conflict_rejects_futures_horizon_on_spot_market() -> None:
    row = {
        "symbol": "BTCUSDT",
        "market": "spot",
        "horizon": "mid",
        "metadata": {"manager_contract_raw_horizon": "USDM-Futures"},
    }

    result = manager_market_horizon_conflict(row)

    assert result == {
        "status": "rejected",
        "reason": "market_horizon_conflict:spot:futures",
        "message": (
            "create_blocks with horizon=futures must set market=futures; "
            "spot/upbit_spot blocks must use short, mid, or long horizons."
        ),
        "input": row,
    }
    assert manager_market_horizon_conflict({"market": "futures", "horizon": "futures"}) is None


def test_validation_repair_metadata_update_merges_repair_into_block_metadata() -> None:
    assert validation_repair_metadata_update(
        {"metadata": {"validation_repair": {"mode": "tighten"}}},
        {"metadata": {"existing": True}},
    ) == {
        "existing": True,
        "validation_repair": {"mode": "tighten"},
    }
    assert validation_repair_metadata_update({}, {"metadata": {"existing": True}}) == {}


def test_validation_repair_metadata_update_merges_wiki_repair_pressure() -> None:
    assert validation_repair_metadata_update(
        {
            "jue_wiki_repair_pressure": "coverage stale for ETHUSDT",
            "period_memory_coverage_gap": "monthly review missing",
            "metadata": {
                "jue_wiki_repair_resolution": "keep as probe until wiki refreshed",
                "period_memory_override_reason": (
                    "current live evidence overrides the review gap"
                ),
            },
        },
        {"metadata": {"existing": True}},
    ) == {
        "existing": True,
        "jue_wiki_repair_pressure": "coverage stale for ETHUSDT",
        "jue_wiki_repair_resolution": "keep as probe until wiki refreshed",
        "period_memory_coverage_gap": "monthly review missing",
        "period_memory_override_reason": (
            "current live evidence overrides the review gap"
        ),
    }


def test_manager_close_has_adverse_evidence_detects_force_and_risk_tokens() -> None:
    assert manager_close_has_adverse_evidence({"force": True}, {}) is True
    assert (
        manager_close_has_adverse_evidence(
            {"metadata": {"risk_note": "spread risk widened"}},
            {"metadata": {"invalidation": ""}},
        )
        is True
    )
    assert manager_close_has_adverse_evidence({"reason": "take note only"}, {}) is False


def test_manager_growth_governor_create_rejection_plans_create_block_limits() -> None:
    row = {"symbol": "BTCUSDT", "market": "futures"}

    assert (
        manager_growth_governor_create_rejection(
            row,
            applies=False,
            growth_governor={"allow_new_blocks": False},
            governed_new_blocks=99,
            max_new_blocks=0,
            waiting_entry=False,
        )
        is None
    )

    assert manager_growth_governor_create_rejection(
        row,
        applies=True,
        growth_governor={"allow_new_blocks": False},
        governed_new_blocks=0,
        max_new_blocks=2,
        waiting_entry=True,
    ) == {
        "status": "rejected",
        "reason": "growth_governor_halt_new_blocks",
        "growth_governor": {"allow_new_blocks": False},
        "input": row,
    }

    assert manager_growth_governor_create_rejection(
        row,
        applies=True,
        growth_governor={"allow_new_blocks": True},
        governed_new_blocks=2,
        max_new_blocks=2,
        waiting_entry=True,
    ) == {
        "status": "rejected",
        "reason": "growth_governor_new_block_limit",
        "growth_governor": {"allow_new_blocks": True},
        "input": row,
    }

    assert manager_growth_governor_create_rejection(
        row,
        applies=True,
        growth_governor={"require_waiting_entry": True},
        governed_new_blocks=0,
        max_new_blocks=2,
        waiting_entry=False,
    ) == {
        "status": "rejected",
        "reason": "growth_governor_requires_waiting_entry",
        "growth_governor": {"require_waiting_entry": True},
        "input": row,
    }


def test_manager_growth_governor_allows_volatile_attack_probe_immediate_entry() -> None:
    row = {
        "symbol": "MEMEUSDT",
        "market": "futures",
        "side": "long",
        "horizon": "futures",
        "lane": "volatile_attack",
        "qty": 5.0,
        "entry_price": 1.0,
        "target_price": 1.08,
        "stop_price": 0.97,
        "calculated": {
            "lane": "volatile_attack",
            "quote_budget_usdt": 7.5,
            "sizing_inputs": {"quote_budget_usdt": 7.5},
        },
    }

    assert (
        manager_growth_governor_create_rejection(
            row,
            applies=True,
            growth_governor={"require_waiting_entry": True},
            growth_unlock={
                "action_permissions": {
                    "volatile_attack_probe": True,
                    "immediate_entry": False,
                }
            },
            governed_new_blocks=0,
            max_new_blocks=2,
            waiting_entry=False,
        )
        is None
    )


def test_manager_create_policy_repair_rejection_prioritizes_policy_then_repair() -> None:
    row = {"symbol": "BTCUSDT", "market": "futures"}

    assert (
        manager_create_policy_repair_rejection(
            row,
            policy_enforcement={},
            repair_enforcement={},
        )
        is None
    )

    assert manager_create_policy_repair_rejection(
        row,
        policy_enforcement={"rejected": True, "reason": "policy_says_wait"},
        repair_enforcement={"rejected": True, "reason": "repair_says_wait"},
    ) == {
        "status": "rejected",
        "reason": "policy_says_wait",
        "policy_effect_enforcement": {
            "rejected": True,
            "reason": "policy_says_wait",
        },
        "input": row,
    }

    assert manager_create_policy_repair_rejection(
        row,
        policy_enforcement={},
        repair_enforcement={"rejected": True},
    ) == {
        "status": "rejected",
        "reason": "validation_repair_rejected",
        "validation_repair_enforcement": {"rejected": True},
        "input": row,
    }


def test_manager_update_fields_allows_entry_price_only_for_proposed_blocks() -> None:
    row = {
        "entry_price": 100.0,
        "target_price": 112.0,
        "reason": "target revised",
        "validation_repair": {"mode": "repair"},
    }

    assert manager_update_fields(row, {"status": "proposed", "metadata": {"old": True}}) == {
        "entry_price": 100.0,
        "target_price": 112.0,
        "llm_reason": "target revised",
        "metadata": {"old": True, "validation_repair": {"mode": "repair"}},
    }
    assert manager_update_fields(row, {"status": "open", "metadata": {"old": True}}) == {
        "target_price": 112.0,
        "llm_reason": "target revised",
        "metadata": {"old": True, "validation_repair": {"mode": "repair"}},
    }


def test_manager_close_and_pause_field_builders_preserve_existing_shapes() -> None:
    assert manager_exit_request_fields({}) == {
        "force_exit_requested": 1,
        "llm_reason": "manager_close_requested",
    }
    assert manager_exit_request_fields({"reason": "risk invalidated"}) == {
        "force_exit_requested": 1,
        "llm_reason": "risk invalidated",
    }
    assert manager_closed_fields({"reason": "stale"}, closed_at="2026-06-21T00:00:00+00:00") == {
        "status": "closed",
        "closed_at": "2026-06-21T00:00:00+00:00",
        "llm_reason": "stale",
    }
    assert manager_pause_fields({}) == {"status": "paused", "llm_reason": ""}
