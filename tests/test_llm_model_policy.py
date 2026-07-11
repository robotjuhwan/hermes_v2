from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace

import pytest


def _policy_module():
    assert importlib.util.find_spec("tradecraft.services.llm_model_policy") is not None
    return importlib.import_module("tradecraft.services.llm_model_policy")


@pytest.fixture
def policy_settings() -> SimpleNamespace:
    return SimpleNamespace(
        llm_model="gpt-5.6-sol",
        llm_reasoning_effort="xhigh",
        llm_reasoning_model="gpt-5.6-terra",
        llm_reasoning_model_effort="high",
        llm_utility_model="gpt-5.6-luna",
        llm_utility_model_effort="medium",
        llm_offline_model="gpt-5.6-sol",
        llm_offline_model_effort="max",
        binance_block_trader_llm_model="gpt-5.6-sol",
        binance_block_trader_llm_reasoning_effort="xhigh",
        crypto_market_research_llm_model="gpt-5.6-terra",
        crypto_market_research_llm_reasoning_effort="high",
        crypto_alpha_llm_model="gpt-5.6-luna",
        crypto_alpha_llm_reasoning_effort="medium",
    )


@pytest.mark.parametrize(
    ("component", "expected_tier", "expected_model", "expected_effort"),
    [
        ("kis_block_manager", "critical", "gpt-5.6-sol", "xhigh"),
        ("binance_block_manager", "critical", "gpt-5.6-sol", "xhigh"),
        ("market_judge", "critical", "gpt-5.6-sol", "xhigh"),
        ("crypto_market_research", "reasoning", "gpt-5.6-terra", "high"),
        ("symbol_analysis", "reasoning", "gpt-5.6-terra", "high"),
        ("investment_memory", "reasoning", "gpt-5.6-terra", "high"),
        ("research_reports", "utility", "gpt-5.6-luna", "medium"),
        ("crypto_alpha", "utility", "gpt-5.6-luna", "medium"),
        ("jue_codex_lab", "offline", "gpt-5.6-sol", "max"),
    ],
)
def test_component_policy_uses_role_specific_56_model(
    policy_settings: SimpleNamespace,
    component: str,
    expected_tier: str,
    expected_model: str,
    expected_effort: str,
) -> None:
    policy = _policy_module().resolve_llm_model_policy(
        policy_settings,
        component=component,
    )

    assert policy.tier == expected_tier
    assert policy.model == expected_model
    assert policy.reasoning_effort == expected_effort


def test_unknown_component_defaults_to_balanced_reasoning_policy(
    policy_settings: SimpleNamespace,
) -> None:
    policy = _policy_module().resolve_llm_model_policy(
        policy_settings,
        component="new_unclassified_component",
    )

    assert policy.tier == "reasoning"
    assert policy.model == "gpt-5.6-terra"
    assert policy.reasoning_effort == "high"


@pytest.mark.parametrize("operation", ["weekly_review", "monthly_review", "policy_revision"])
def test_investment_memory_policy_revision_uses_offline_max(
    policy_settings: SimpleNamespace,
    operation: str,
) -> None:
    policy = _policy_module().resolve_llm_model_policy(
        policy_settings,
        component="investment_memory",
        operation=operation,
    )

    assert policy.tier == "offline"
    assert policy.model == "gpt-5.6-sol"
    assert policy.reasoning_effort == "max"


def test_policy_exposes_memory_operation_overrides(
    policy_settings: SimpleNamespace,
) -> None:
    overrides = _policy_module().llm_operation_model_overrides(
        policy_settings,
        component="investment_memory",
    )

    assert overrides == {
        "monthly_review": ("gpt-5.6-sol", "max"),
        "policy_revision": ("gpt-5.6-sol", "max"),
        "weekly_review": ("gpt-5.6-sol", "max"),
    }
