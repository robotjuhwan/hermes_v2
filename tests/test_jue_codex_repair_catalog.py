from __future__ import annotations

from tradecraft.services.jue_codex_repair_catalog import repair_strategy_for


def test_catalog_maps_cost_fail_to_cost_repair_strategy() -> None:
    strategy = repair_strategy_for(
        venue="binance",
        discipline_id="cost_simulation",
        automation_hook="sync_live_performance_and_edges",
        failure_status="fail",
    )

    assert strategy["owner"] == "cost_model"
    assert "src/tradecraft/services/live_performance.py" in strategy["allowed_paths"]
    assert ".env" in strategy["blocked_paths"]
    assert ".runtime" in strategy["blocked_paths"]
    assert "pytest tests/test_live_performance.py" in strategy["verification_commands"]
    assert strategy["green_condition"] == {
        "discipline_id": "cost_simulation",
        "target_statuses": ["pass", "warn"],
    }
