from __future__ import annotations

from tradecraft.api.jue_workflow_payloads import (
    PREFERRED_JUE_WORKFLOW_IDS,
    available_jue_workflow_ids,
)


def test_available_jue_workflow_ids_returns_preferred_then_extra_sorted(tmp_path) -> None:
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    for workflow_id in ("z_extra", "kis_post_close", "a_extra", "kis_pre_open"):
        (workflow_dir / f"{workflow_id}.json").write_text("{}", encoding="utf-8")

    assert available_jue_workflow_ids(workflow_dir) == [
        "kis_pre_open",
        "kis_post_close",
        "a_extra",
        "z_extra",
    ]


def test_preferred_jue_workflow_ids_preserve_active_trading_order() -> None:
    assert PREFERRED_JUE_WORKFLOW_IDS[:3] == (
        "kis_pre_open",
        "kis_intraday_manager",
        "kis_post_close",
    )
    assert "binance_cycle" in PREFERRED_JUE_WORKFLOW_IDS
