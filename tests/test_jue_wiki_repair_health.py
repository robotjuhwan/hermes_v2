from __future__ import annotations

from datetime import datetime, timezone

from tradecraft.services.jue_wiki_repair_health import (
    WikiRepairHealthPolicy,
    evaluate_repair_queue_health,
)


def test_progressing_open_queue_is_advisory_not_warning() -> None:
    health = evaluate_repair_queue_health(
        {
            "open_count": 346,
            "oldest_open_at": "2026-07-09T00:00:00+00:00",
            "last_resolved_at": "2026-07-10T00:55:00+00:00",
            "opened_in_window": 20,
            "resolved_in_window": 40,
        },
        policy=WikiRepairHealthPolicy(),
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )

    assert health["status"] == "progressing"
    assert health["warning_signals"] == []
    assert health["advisory_signals"] == ["jue_wiki_repair_queue_open"]
    assert health["net_growth_in_window"] == -20


def test_stalled_overdue_queue_is_operational_warning() -> None:
    health = evaluate_repair_queue_health(
        {
            "open_count": 25,
            "oldest_open_at": "2026-07-07T00:00:00+00:00",
            "last_resolved_at": "2026-07-07T00:00:00+00:00",
            "opened_in_window": 30,
            "resolved_in_window": 0,
        },
        policy=WikiRepairHealthPolicy(),
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )

    assert health["status"] == "warning"
    assert "jue_wiki_repair_queue_overdue" in health["warning_signals"]
    assert "jue_wiki_repair_queue_stalled" in health["warning_signals"]
    assert "jue_wiki_repair_queue_growing" in health["warning_signals"]


def test_empty_queue_is_idle_without_signals() -> None:
    health = evaluate_repair_queue_health(
        {
            "open_count": 0,
            "opened_in_window": 4,
            "resolved_in_window": 4,
        },
        policy=WikiRepairHealthPolicy(),
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )

    assert health["status"] == "idle"
    assert health["warning_signals"] == []
    assert health["advisory_signals"] == []
