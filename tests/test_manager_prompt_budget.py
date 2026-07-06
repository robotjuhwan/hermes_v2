from __future__ import annotations

import json
from typing import Any

from tradecraft.services.manager_prompt_budget import (
    attach_jue_wiki_budget_report,
    attach_prompt_budget,
    enforce_manager_prompt_budget_with_report,
    format_prompt_budget_alert_message,
    prompt_budget_error,
)


def _chars(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _section_rows(prompt: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"section": key, "chars": _chars({key: value})}
        for key, value in prompt.items()
        if key not in {"prompt_budget", "prompt_compaction"}
    ]
    return sorted(rows, key=lambda row: int(row["chars"]), reverse=True)


def test_attach_prompt_budget_keeps_required_sections_even_when_small() -> None:
    prompt: dict[str, Any] = {
        **{f"large_{idx}": "x" * (500 - idx) for idx in range(20)},
        "research_spine": {"summary": "compact but required"},
        "strategy": {"summary": "also required"},
    }

    attach_prompt_budget(
        prompt,
        target_chars=1_000,
        warn_chars=1_200,
        max_chars=1_300,
        section_size_rows=_section_rows,
        prompt_chars=_chars,
        policy="shared prompt budget policy",
        required_sections=("research_spine", "strategy"),
    )

    section_names = [row["section"] for row in prompt["prompt_budget"]["sections"]]
    assert "research_spine" in section_names
    assert "strategy" in section_names
    assert prompt["prompt_budget"]["policy"] == "shared prompt budget policy"
    assert prompt["prompt_budget"]["over_target"] is True
    assert prompt["prompt_budget"]["over_max"] is True


def test_prompt_budget_error_and_alert_message_are_shared() -> None:
    prompt = {
        "prompt_budget": {
            "over_max": True,
            "total_chars": 190_100,
            "target_chars": 150_000,
            "warn_chars": 180_000,
            "max_chars": 190_000,
        }
    }

    error = prompt_budget_error(prompt)
    message = format_prompt_budget_alert_message(
        venue="Binance",
        run_id=42,
        error_message=error,
        prompt=prompt,
    )

    assert error == "prompt_budget_exceeded: total_chars=190100 max_chars=190000"
    assert "[HERMES] Binance 쥬 판단 입력 상한 초과" in message
    assert "- run_id: 42" in message
    assert "- target/warn/max: 150,000 / 180,000 / 190,000" in message


def test_budget_report_tracks_wiki_live_and_raw_sections() -> None:
    payload = {
        "account": {"cash": 1_000_000},
        "jue_wiki": {"content": "wiki" * 100, "pages": ["kis.symbol.005930"]},
        "raw_rag": {"content": "rag" * 1000},
    }

    trimmed, report = enforce_manager_prompt_budget_with_report(
        payload,
        max_chars=5_000,
        protected_keys=("account", "jue_wiki", "raw_rag"),
    )

    assert "account" in trimmed
    assert "jue_wiki" in trimmed
    assert "raw_rag" in trimmed
    assert report["max_chars"] == 5_000
    assert report["sections"]["account"]["protected"] is True
    assert report["sections"]["jue_wiki"]["protected"] is True
    assert report["sections"]["raw_rag"]["protected"] is True
    assert report["total_chars"] <= 5_000


def test_attach_jue_wiki_budget_report_marks_expected_sections_protected() -> None:
    prompt = {
        "account": {"cash": 1_000_000},
        "jue_wiki": {"content": "wiki", "pages": ["kis.symbol.005930"]},
        "live_authority": {"status": "open"},
        "market_pulse": {"regime": "risk_on"},
        "crypto_market_pulse": {"regime": "neutral"},
        "raw_context_refs": {"items": ["ref-1"]},
        "raw_rag": {"content": "rag"},
        "optional_notes": "x" * 500,
    }

    attach_jue_wiki_budget_report(prompt, max_chars=5_000)

    report = prompt["jue_wiki_budget_report"]
    for key in (
        "jue_wiki",
        "account",
        "live_authority",
        "market_pulse",
        "crypto_market_pulse",
        "raw_context_refs",
        "raw_rag",
    ):
        assert report["sections"][key]["protected"] is True
    assert report["sections"]["optional_notes"]["protected"] is False


def test_attach_jue_wiki_budget_report_reports_actual_prompt_over_budget() -> None:
    prompt = {
        "account": {"cash": 1_000_000},
        "jue_wiki": {"content": "wiki", "pages": ["kis.symbol.005930"]},
        "optional_notes": "x" * 10_000,
    }
    original_prompt = json.loads(json.dumps(prompt, ensure_ascii=False))

    attach_jue_wiki_budget_report(prompt, max_chars=1_000)

    report = prompt["jue_wiki_budget_report"]
    prompt_without_report = {
        key: value for key, value in prompt.items() if key != "jue_wiki_budget_report"
    }
    assert prompt_without_report == original_prompt
    assert report["status"] == "over_budget"
    assert report["total_chars"] > report["max_chars"]
    assert report["original_total_chars"] == report["total_chars"]
    assert report["projected_total_chars"] <= report["total_chars"]
