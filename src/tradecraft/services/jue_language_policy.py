from __future__ import annotations

from typing import Any


TRANSLATE_FOR_DISPLAY_FIELDS = [
    "title",
    "message_md",
    "summary",
    "summary_md",
    "review_md",
    "reason",
    "reason_md",
    "reasons",
    "risks",
    "risk_note",
    "triggers",
    "data_gaps",
    "thesis",
    "observations",
    "hold_decision",
    "hold_decision.summary",
    "hold_decision.reasons",
    "hold_decision.next_triggers.condition",
    "hold_decision.next_triggers.reason",
    "hold_decision.data_gaps",
    "hold_decision.risk_notes",
    "create_blocks.thesis",
    "create_blocks.risk_note",
    "update_blocks.reason",
    "close_blocks.reason",
    "pause_blocks.reason",
]


def jue_language_policy(
    *,
    extra_display_fields: list[str] | None = None,
) -> dict[str, Any]:
    translate_fields = list(dict.fromkeys([
        *TRANSLATE_FOR_DISPLAY_FIELDS,
        *(extra_display_fields or []),
    ]))
    return {
        "version": "jue_language_policy_v1",
        "user_facing_language": "ko-KR",
        "internal_prompt_language": "en-US",
        "internal_reasoning_language": "en-US",
        "operator_display_language": "ko-KR",
        "internal_process": [
            (
                "Perform all analysis, hypothesis generation, scoring, ranking, "
                "risk checks, block design, and draft conclusions in English."
            ),
            (
                "For every operator-facing field, first draft the conclusion in "
                "English internally, then translate the final display text into "
                "natural Korean."
            ),
            (
                "Return Korean display text to the operator unless the schema "
                "explicitly requests an English *_en field."
            ),
        ],
        "user_visible_generation_order": (
            "draft_conclusion_in_english_then_translate_to_korean_for_display"
        ),
        "english_only_internal_fields": [
            "analysis",
            "hypotheses",
            "scoring",
            "ranking",
            "risk_review",
            "block_design",
            "draft_conclusion",
            "decision_rationale",
            "policy_revision_reasoning",
        ],
        "translate_for_display_fields": translate_fields,
        "applies_to": translate_fields,
        "preserve_original_language_fields": [
            "symbol",
            "ticker",
            "code",
            "name",
            "source_title",
            "quoted_evidence",
            "user_directives",
            "raw_evidence",
        ],
        "do_not_expose_english_draft": True,
        "rule": (
            "Jue thinks and drafts in English. Only the final operator-visible "
            "conclusion is translated into Korean and returned."
        ),
    }
