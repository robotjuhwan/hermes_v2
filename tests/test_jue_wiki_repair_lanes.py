from __future__ import annotations

from tradecraft.services.jue_wiki_repair_lanes import (
    REPAIR_ACTION_LANES,
    classify_repair_action,
)


CURRENT_ACTION_TYPES = {
    "book_depth_gap",
    "collect_or_rebuild_requested_symbol_wiki_summary",
    "cross_check_evidence_quality",
    "edge_rebuild",
    "mark_unresolved",
    "missing_jue_wiki_action_reference",
    "refresh_requested_symbol_summary",
    "refresh_symbol_financials",
    "refresh_symbol_fundamentals",
    "repair_quality_warning_effectiveness",
    "repair_research_source_schema",
    "repair_usage_guidance_contract",
    "reproject_closed_block_outcome_horizons",
}


def test_every_current_repair_action_type_has_an_explicit_lane() -> None:
    assert CURRENT_ACTION_TYPES - set(REPAIR_ACTION_LANES) == set()


def test_unknown_repair_action_fails_closed_to_integrity() -> None:
    classification = classify_repair_action("future_unknown_action")

    assert classification.lane == "integrity"
    assert classification.registered is False


def test_strategy_and_evidence_actions_keep_separate_ownership() -> None:
    assert classify_repair_action("book_depth_gap").lane == "strategy"
    assert classify_repair_action("edge_rebuild").lane == "strategy"
    assert classify_repair_action("refresh_symbol_financials").lane == "evidence"
    assert classify_repair_action("repair_usage_guidance_contract").lane == "evidence"
    assert classify_repair_action("missing_jue_wiki_action_reference").lane == "strategy"
    assert classify_repair_action("repair_research_source_schema").lane == "integrity"
