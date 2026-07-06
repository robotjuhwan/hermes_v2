from __future__ import annotations

from tradecraft.services.kis_snapshot import (
    compact_kis_manager_run,
    history_kis_block_rows,
    visible_kis_block_rows,
)


def test_visible_kis_block_rows_keeps_open_style_statuses() -> None:
    blocks = [
        {"block_id": "proposed", "status": "proposed"},
        {"block_id": "open", "status": "open"},
        {"block_id": "exit", "status": "exit_pending"},
        {"block_id": "closed", "status": "closed"},
        {"block_id": "error", "status": "error"},
    ]

    assert [row["block_id"] for row in visible_kis_block_rows(blocks)] == [
        "proposed",
        "open",
        "exit",
    ]


def test_history_kis_block_rows_excludes_visible_statuses_and_limits() -> None:
    blocks = [
        {"block_id": "open", "status": "open"},
        {"block_id": "closed-1", "status": "closed"},
        {"block_id": "paused", "status": "paused"},
        {"block_id": "error", "status": "error"},
    ]

    assert [row["block_id"] for row in history_kis_block_rows(blocks, limit=2)] == [
        "closed-1",
        "paused",
    ]


def test_compact_kis_manager_run_keeps_public_fields_and_action_counts() -> None:
    row = {
        "id": 9,
        "run_at": "2026-06-21T00:00:00+09:00",
        "market_session": "regular",
        "status": "ok",
        "mode": "llm",
        "model": "gpt-5.5",
        "error_message": "x" * 600,
        "workflow_id": "wf",
        "workflow_version": "v2",
        "skill_ids": [str(i) for i in range(10)],
        "contract_ids": [str(i) for i in range(10)],
        "actions": {
            "create_blocks": [{"symbol": "005930"}],
            "close_blocks": [{"block_id": "b1"}, {"block_id": "b2"}],
            "note": "ignored",
        },
        "applied": {
            "status": "ok",
            "created_count": 1,
            "updated_count": 2,
            "closed_count": 3,
            "secret": "drop",
        },
        "response": {
            "no_action_watch": {
                "status": "attention",
                "streak": 3,
                "reason": "aggressive_candidates_seen_but_no_block_action",
            },
            "latest_input_summary": {
                "status": "ok",
                "aggressive_candidate_count": 5,
            },
        },
        "hold_decision": {"summary": "관망"},
        "creative_hypotheses": [{"id": i} for i in range(6)],
    }

    compact = compact_kis_manager_run(
        row,
        clean_text=lambda value, *, limit: str(value)[:limit],
    )

    assert compact["id"] == 9
    assert compact["error_message"] == "x" * 500
    assert compact["skill_ids"] == [str(i) for i in range(8)]
    assert compact["contract_ids"] == [str(i) for i in range(8)]
    assert compact["action_counts"] == {
        "create_blocks": 1,
        "close_blocks": 2,
    }
    assert compact["applied"] == {
        "status": "ok",
        "created_count": 1,
        "updated_count": 2,
        "closed_count": 3,
    }
    assert compact["hold_decision"] == {"summary": "관망"}
    assert compact["creative_hypotheses"] == [{"id": i} for i in range(4)]
    assert compact["no_action_watch"]["status"] == "attention"
    assert compact["no_action_watch"]["streak"] == 3
    assert compact["latest_input_summary"]["aggressive_candidate_count"] == 5


def test_compact_kis_manager_run_preserves_missing_status() -> None:
    assert compact_kis_manager_run({"status": "missing"}) == {"status": "missing"}
