from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RepairLane = Literal["integrity", "evidence", "strategy"]

REPAIR_ACTION_LANES: dict[str, RepairLane] = {
    "book_depth_gap": "strategy",
    "collect_or_rebuild_requested_symbol_wiki_summary": "evidence",
    "cross_check_evidence_quality": "evidence",
    "edge_rebuild": "strategy",
    "mark_unresolved": "integrity",
    "missing_jue_wiki_action_reference": "strategy",
    "refresh_requested_symbol_summary": "evidence",
    "refresh_symbol_financials": "evidence",
    "refresh_symbol_fundamentals": "evidence",
    "repair_quality_warning_effectiveness": "evidence",
    "repair_research_source_schema": "integrity",
    "repair_usage_guidance_contract": "evidence",
    "reproject_closed_block_outcome_horizons": "evidence",
}


@dataclass(frozen=True)
class RepairLaneClassificationV1:
    lane: RepairLane
    registered: bool


def classify_repair_action(action_type: str) -> RepairLaneClassificationV1:
    clean_action_type = str(action_type or "").strip()
    lane = REPAIR_ACTION_LANES.get(clean_action_type)
    if lane is None:
        return RepairLaneClassificationV1(lane="integrity", registered=False)
    return RepairLaneClassificationV1(lane=lane, registered=True)
