from __future__ import annotations

from pathlib import Path

from tradecraft.services import kis_manager_actions as kis_manager_actions_module
from tradecraft.services.kis_manager_actions import (
    manager_close_action_plan,
    manager_update_action_plan,
    sanitize_kis_manager_actions,
)


def test_kis_block_trader_does_not_reown_manager_action_metadata_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def _decision_metadata_fields(" not in source


def test_decision_metadata_schema_lives_with_kis_manager_actions() -> None:
    schema = kis_manager_actions_module.DECISION_METADATA_OUTPUT_SCHEMA

    assert schema["target_block_value_krw"].startswith("optional number")
    assert schema["post_review_required"].startswith("optional boolean")
    for key in kis_manager_actions_module.DECISION_METADATA_NUMERIC_FIELDS:
        assert key in schema
    for key in kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS:
        assert key in schema


def test_decision_metadata_schema_exposes_jue_wiki_repair_pressure_resolution() -> None:
    schema = kis_manager_actions_module.DECISION_METADATA_OUTPUT_SCHEMA

    assert "jue_wiki_repair_pressure" in schema
    assert "repair pressure" in schema["jue_wiki_repair_pressure"]
    assert "jue_wiki_repair_resolution" in schema
    assert "jue_wiki_memory_card_quality" in schema
    assert "thin Wiki memory card" in schema["jue_wiki_memory_card_quality"]
    assert "jue_wiki_memory_card_cross_check" in schema
    assert "jue_wiki_repair_pressure" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )
    assert "jue_wiki_repair_resolution" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )
    assert "jue_wiki_memory_card_quality" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )
    assert "jue_wiki_memory_card_cross_check" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )
    assert "period_memory_coverage_gap" in schema
    assert "weekly/monthly review or replay" in schema["period_memory_coverage_gap"]
    assert "period_memory_override_reason" in schema
    assert "current live evidence overrides" in schema[
        "period_memory_override_reason"
    ]
    assert "metadata_contract_audit_resolution" in schema
    assert "metadata contract audit" in schema["metadata_contract_audit_resolution"]
    assert "period_memory_coverage_gap" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )
    assert "period_memory_override_reason" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )
    assert "metadata_contract_audit_resolution" in (
        kis_manager_actions_module.DECISION_METADATA_TEXT_FIELDS
    )


def test_manager_update_action_plan_rejects_missing_block() -> None:
    plan = manager_update_action_plan(
        row={"block_id": "missing", "target_price": 120000, "stop_price": 90000},
        current=None,
        quote={},
    )

    assert plan == {
        "status": "rejected",
        "reason": "block_missing",
        "block_id": "missing",
    }


def test_manager_update_action_plan_rejects_invalid_target_stop_bounds() -> None:
    plan = manager_update_action_plan(
        row={"block_id": "block-1", "target_price": 95000, "stop_price": 90000},
        current={
            "block_id": "block-1",
            "symbol": "005930",
            "entry_price": 100000,
            "target_price": 120000,
            "stop_price": 80000,
        },
        quote={"price": 100000},
    )

    assert plan["status"] == "rejected"
    assert plan["reason"] == "invalid_update_target_stop_bounds"
    assert plan["block_id"] == "block-1"
    assert plan["symbol"] == "005930"
    assert plan["reference_price"] == 100000.0
    assert plan["target_price"] == 95000.0
    assert plan["stop_price"] == 90000.0
    assert plan["metadata_event"] == {
        "event_type": "manager_update_rejected",
        "message": "manager update rejected by target/stop bounds",
    }


def test_manager_update_action_plan_builds_update_fields() -> None:
    plan = manager_update_action_plan(
        row={"block_id": "block-1", "target_price": 130000, "reason": "raise target"},
        current={
            "block_id": "block-1",
            "symbol": "005930",
            "entry_price": 100000,
            "target_price": 120000,
            "stop_price": 80000,
        },
        quote={"price": 101000},
    )

    assert plan == {
        "status": "update",
        "block_id": "block-1",
        "fields": {
            "target_price": 130000,
            "stop_price": None,
            "llm_reason": "raise target",
        },
    }


def test_manager_close_action_plan_handles_missing_waiting_deferred_and_exit() -> None:
    assert manager_close_action_plan(
        row={"block_id": "missing", "reason": "risk"},
        block=None,
        is_waiting_entry=False,
        close_guard={"allowed": True},
    ) == {
        "status": "rejected",
        "reason": "block_missing",
        "block_id": "missing",
    }

    assert manager_close_action_plan(
        row={"block_id": "wait-1", "reason": "cancel setup"},
        block={"block_id": "wait-1", "status": "proposed"},
        is_waiting_entry=True,
        close_guard={"allowed": True},
    ) == {
        "status": "close_waiting_entry",
        "block_id": "wait-1",
        "reason": "cancel setup",
        "metadata_event_type": "manager_close_decision_metadata",
        "metadata_message": "cancel setup",
    }

    assert manager_close_action_plan(
        row={"block_id": "open-1", "reason": "too early"},
        block={"block_id": "open-1", "status": "open"},
        is_waiting_entry=False,
        close_guard={"allowed": False, "reason": "horizon_patience"},
    ) == {
        "status": "defer",
        "block_id": "open-1",
        "event_type": "manager_close_deferred",
        "event_message": "manager close deferred by horizon patience guard",
        "event_payload": {
            "allowed": False,
            "reason": "horizon_patience",
            "requested_reason": "too early",
            "decision_class": None,
            "close_trigger": None,
        },
        "rejection": {
            "action": "close",
            "allowed": False,
            "reason": "horizon_patience",
        },
    }

    assert manager_close_action_plan(
        row={"block_id": "open-1", "reason": "thesis invalidated"},
        block={"block_id": "open-1", "status": "open"},
        is_waiting_entry=False,
        close_guard={"allowed": True},
    ) == {
        "status": "request_exit",
        "block_id": "open-1",
        "fields": {
            "force_exit_requested": 1,
            "llm_reason": "thesis invalidated",
        },
        "metadata_event_type": "manager_close_decision_metadata",
        "metadata_message": "thesis invalidated",
    }


def test_sanitize_kis_manager_actions_unwraps_contract_and_normalizes_rows() -> None:
    actions = sanitize_kis_manager_actions(
        {
            "selected_contract_id": "block_action_contract",
            "payload": {
                "adopt_existing_blocks": [
                    {
                        "symbol": "005930",
                        "qty": "2.8",
                        "target_price": "78000",
                        "stop_price": "69000",
                        "horizon": "중기",
                        "allocation_reason": "사용자 보유분을 중기 블록화",
                        "thesis": "value pullback",
                        "confidence": "0.72",
                        "risk_note": "earnings gap",
                    },
                    {
                        "symbol": "005930",
                        "qty": 10,
                        "target_price": 78000,
                        "stop_price": 69000,
                    },
                ],
                "create_blocks": [
                    {
                        "symbol": "000660",
                        "qty": "1",
                        "target_price": "190000",
                        "stop_price": "170000",
                        "entry_style": "pullback",
                        "entry_trigger_price": "175000",
                        "entry_trigger_operator": "",
                        "horizon": "long-term",
                        "entry_quality": "pullback reclaim",
                        "entry_quality_score": "67.5",
                        "pullback_confirmed": "true",
                        "target_block_value_krw": "175000",
                        "decision_class": "create",
                        "jue_wiki_repair_pressure": "financials page stale",
                        "jue_wiki_repair_resolution": "small waiting block only",
                        "jue_wiki_memory_card_quality": "weak memory card",
                        "jue_wiki_memory_card_cross_check": (
                            "checked live report and flow"
                        ),
                        "period_memory_coverage_gap": "kis weekly replay missing",
                        "period_memory_override_reason": (
                            "current live evidence overrides the replay gap"
                        ),
                    }
                ],
                "update_blocks": [
                    {
                        "block_id": "kis-open-1",
                        "target_price": "121000",
                        "stop_price": "99000",
                        "reason": "raise target",
                        "jue_wiki_repair_pressure": "coverage omitted",
                        "jue_wiki_memory_card_cross_check": "checked live quote",
                        "period_memory_coverage_gap": "monthly review missing",
                    },
                    {"block_id": "unknown", "reason": "ignored"},
                ],
                "close_blocks": [
                    {
                        "block_id": "kis-open-1",
                        "reason": "thesis invalidated",
                        "close_trigger": "thesis_invalidated",
                        "jue_wiki_repair_resolution": "close addresses stale thesis",
                        "jue_wiki_memory_card_quality": "thin memory reduced confidence",
                        "period_memory_override_reason": (
                            "close uses live adverse evidence despite replay gap"
                        ),
                    }
                ],
                "pause_blocks": [{"block_id": "missing", "reason": "ignored"}],
            },
        },
        blocks=[
            {
                "block_id": "kis-open-1",
                "symbol": "277810",
                "status": "open",
                "qty_open": 1,
            }
        ],
        quotes={
            "005930": {"price": 72000},
            "000660": {"price": 180000},
        },
        account={
            "positions": [
                {"symbol": "005930", "available_qty": 3, "mark_price": 72000},
            ],
        },
    )

    assert actions["adopt_existing_blocks"] == [
        {
            "symbol": "005930",
            "qty": 2,
            "target_price": 78000.0,
            "stop_price": 69000.0,
            "horizon": "mid",
            "allocation_reason": "사용자 보유분을 중기 블록화",
            "thesis": "value pullback",
            "confidence": 0.72,
            "risk_note": "earnings gap",
        }
    ]
    assert actions["create_blocks"] == [
        {
            "symbol": "000660",
            "qty": 1,
            "target_price": 190000.0,
            "stop_price": 170000.0,
            "entry_style": "wait_for_price",
            "entry_trigger_price": 175000.0,
            "entry_trigger_operator": "lte",
            "horizon": "long",
            "allocation_reason": "",
            "thesis": "",
            "confidence": 0.0,
            "risk_note": "",
            "entry_quality": "pullback reclaim",
            "entry_quality_score": 67.5,
            "pullback_confirmed": True,
            "target_block_value_krw": 175000.0,
            "decision_class": "create",
            "jue_wiki_repair_pressure": "financials page stale",
            "jue_wiki_repair_resolution": "small waiting block only",
            "jue_wiki_memory_card_quality": "weak memory card",
            "jue_wiki_memory_card_cross_check": "checked live report and flow",
            "period_memory_coverage_gap": "kis weekly replay missing",
            "period_memory_override_reason": (
                "current live evidence overrides the replay gap"
            ),
        }
    ]
    assert actions["update_blocks"] == [
        {
            "block_id": "kis-open-1",
            "target_price": 121000.0,
            "stop_price": 99000.0,
            "reason": "raise target",
            "jue_wiki_repair_pressure": "coverage omitted",
            "jue_wiki_memory_card_cross_check": "checked live quote",
            "period_memory_coverage_gap": "monthly review missing",
                "period_memory_contract_audit": {
                    "status": "missing_override_reason",
                    "policy_id": "period_memory_coverage.missing_override_reason",
                    "gap": "monthly review missing",
                    "override_reason": "",
                    "missing_metadata": ["period_memory_override_reason"],
                    "required_metadata": [
                        "period_memory_coverage_gap",
                        "period_memory_override_reason",
                    ],
                    "repair_action": "add_period_memory_override_reason_before_scaling",
                },
            }
        ]
    assert actions["close_blocks"] == [
        {
            "block_id": "kis-open-1",
            "reason": "thesis invalidated",
            "close_trigger": "thesis_invalidated",
            "jue_wiki_repair_resolution": "close addresses stale thesis",
            "jue_wiki_memory_card_quality": "thin memory reduced confidence",
            "period_memory_override_reason": (
                "close uses live adverse evidence despite replay gap"
            ),
                "period_memory_contract_audit": {
                    "status": "missing_coverage_gap",
                    "policy_id": "period_memory_coverage.missing_coverage_gap",
                    "gap": "",
                    "override_reason": (
                        "close uses live adverse evidence despite replay gap"
                    ),
                    "missing_metadata": ["period_memory_coverage_gap"],
                    "required_metadata": [
                        "period_memory_coverage_gap",
                        "period_memory_override_reason",
                    ],
                    "repair_action": (
                        "name_period_memory_coverage_gap_before_using_override"
                    ),
                },
            }
        ]
    assert actions["pause_blocks"] == []


def test_sanitize_kis_manager_actions_marks_period_memory_metadata_contract_gaps() -> None:
    actions = sanitize_kis_manager_actions(
        {
            "create_blocks": [
                {
                    "symbol": "000660",
                    "qty": 1,
                    "target_price": 190000,
                    "stop_price": 170000,
                    "entry_style": "wait_for_price",
                    "entry_trigger_price": 175000,
                    "entry_trigger_operator": "lte",
                    "period_memory_coverage_gap": "kis weekly replay missing",
                    "metadata_contract_repair_note": (
                        "metadata contract repair: "
                        "add_period_memory_override_reason_before_scaling; "
                        "resolution: kept waiting probe until override reason is restored"
                    ),
                    "metadata_contract_audit_resolution": (
                        "kept waiting probe until override reason is restored"
                    ),
                }
            ],
            "close_blocks": [
                {
                    "block_id": "kis-open-1",
                    "reason": "live adverse evidence",
                    "period_memory_override_reason": (
                        "current adverse evidence overrides memory gap"
                    ),
                }
            ],
        },
        blocks=[{"block_id": "kis-open-1", "symbol": "005930", "status": "open"}],
        quotes={"000660": {"price": 180000}},
        account={},
    )

    assert actions["create_blocks"][0]["period_memory_contract_audit"] == {
        "status": "missing_override_reason",
        "policy_id": "period_memory_coverage.missing_override_reason",
        "gap": "kis weekly replay missing",
        "override_reason": "",
        "metadata_contract_audit_resolution": (
            "kept waiting probe until override reason is restored"
        ),
        "metadata_contract_repair_note": (
            "metadata contract repair: "
            "add_period_memory_override_reason_before_scaling; "
            "resolution: kept waiting probe until override reason is restored"
        ),
        "missing_metadata": ["period_memory_override_reason"],
        "required_metadata": [
            "period_memory_coverage_gap",
            "period_memory_override_reason",
        ],
        "repair_action": "add_period_memory_override_reason_before_scaling",
    }
    assert actions["create_blocks"][0]["metadata_contract_audit_resolution"] == (
        "kept waiting probe until override reason is restored"
    )
    assert actions["create_blocks"][0]["metadata_contract_repair_note"] == (
        "metadata contract repair: "
        "add_period_memory_override_reason_before_scaling; "
        "resolution: kept waiting probe until override reason is restored"
    )
    assert actions["close_blocks"][0]["period_memory_contract_audit"] == {
        "status": "missing_coverage_gap",
        "policy_id": "period_memory_coverage.missing_coverage_gap",
        "gap": "",
        "override_reason": "current adverse evidence overrides memory gap",
        "missing_metadata": ["period_memory_coverage_gap"],
        "required_metadata": [
            "period_memory_coverage_gap",
            "period_memory_override_reason",
        ],
        "repair_action": "name_period_memory_coverage_gap_before_using_override",
    }
