from __future__ import annotations

from typing import Any


COMMON_BLOCKED_PATHS = [
    ".env",
    ".env.*",
    ".runtime",
    "src/tradecraft/services/binance_adapter.py",
    "src/tradecraft/services/kis_adapter.py",
    "src/tradecraft/services/binance_executor.py",
    "src/tradecraft/services/kis_executor.py",
]

PATTERN_LAB_DISCIPLINES = {
    "overfit_validation",
    "walk_forward_analysis",
    "out_of_sample_test",
    "wfa",
    "oos",
    "overfit",
}


def repair_strategy_for(
    venue: str,
    discipline_id: str,
    automation_hook: str,
    failure_status: str,
) -> dict[str, Any]:
    normalized_discipline = str(discipline_id or "").strip()
    normalized_hook = str(automation_hook or "").strip()
    normalized_venue = str(venue or "").strip().lower()
    normalized_failure = str(failure_status or "").strip().lower()
    target_statuses = ["pass", "warn"]

    if normalized_discipline == "cost_simulation":
        return {
            "version": "jue_codex_repair_strategy_v1",
            "venue": normalized_venue,
            "discipline_id": normalized_discipline,
            "failure_status": normalized_failure,
            "owner": "cost_model",
            "automation_hook": normalized_hook or "sync_live_performance_and_edges",
            "allowed_paths": [
                "src/tradecraft/services/live_performance.py",
                "tests/test_live_performance.py",
            ],
            "blocked_paths": COMMON_BLOCKED_PATHS.copy(),
            "verification_commands": ["pytest tests/test_live_performance.py"],
            "green_condition": {
                "discipline_id": normalized_discipline,
                "target_statuses": target_statuses,
            },
        }

    if normalized_discipline in PATTERN_LAB_DISCIPLINES:
        return {
            "version": "jue_codex_repair_strategy_v1",
            "venue": normalized_venue,
            "discipline_id": normalized_discipline,
            "failure_status": normalized_failure,
            "owner": "pattern_lab",
            "automation_hook": normalized_hook or "pattern_lab_rebuild_wfa_oos",
            "allowed_paths": [
                "src/tradecraft/services/crypto_pattern_lab.py",
                "src/tradecraft/services/kr_equity_pattern_lab.py",
                "tests/test_crypto_pattern_lab.py",
                "tests/test_kr_equity_pattern_lab.py",
            ],
            "blocked_paths": COMMON_BLOCKED_PATHS.copy(),
            "verification_commands": [
                "pytest tests/test_crypto_pattern_lab.py "
                "tests/test_kr_equity_pattern_lab.py"
            ],
            "green_condition": {
                "discipline_id": normalized_discipline,
                "target_statuses": target_statuses,
            },
        }

    return {
        "version": "jue_codex_repair_strategy_v1",
        "venue": normalized_venue,
        "discipline_id": normalized_discipline,
        "failure_status": normalized_failure,
        "owner": "risk_engine",
        "automation_hook": normalized_hook or "refresh_risk_budget_passport",
        "allowed_paths": [
            "src/tradecraft/services/trading_validation.py",
            "src/tradecraft/services/live_authority.py",
            "tests/test_trading_validation.py",
            "tests/test_live_authority.py",
        ],
        "blocked_paths": COMMON_BLOCKED_PATHS.copy(),
        "verification_commands": [
            "pytest tests/test_trading_validation.py tests/test_live_authority.py"
        ],
        "green_condition": {
            "discipline_id": normalized_discipline,
            "target_statuses": target_statuses,
        },
    }
