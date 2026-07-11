from __future__ import annotations

from pathlib import Path


PREFERRED_JUE_WORKFLOW_IDS: tuple[str, ...] = (
    "kis_pre_open",
    "kis_intraday_manager",
    "kis_post_close",
    "block_reflection",
    "policy_revision",
    "crypto_research",
    "binance_cycle",
)


def available_jue_workflow_ids(workflow_dir: str | Path) -> list[str]:
    directory = Path(workflow_dir)
    discovered = sorted(
        path.stem for path in directory.glob("*.json") if path.is_file()
    )
    preferred = [
        workflow_id
        for workflow_id in PREFERRED_JUE_WORKFLOW_IDS
        if workflow_id in discovered
    ]
    preferred_set = set(preferred)
    return preferred + [
        workflow_id for workflow_id in discovered if workflow_id not in preferred_set
    ]
