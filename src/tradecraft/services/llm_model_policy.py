from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CRITICAL_COMPONENTS = frozenset(
    {
        "binance_block_manager",
        "kis_block_manager",
        "market_judge",
    }
)
REASONING_COMPONENTS = frozenset(
    {
        "crypto_market_research",
        "daily_discovery",
        "investment_memory",
        "portfolio_coach",
        "research_ask",
        "research_pipeline",
        "strategy_intelligence",
        "symbol_analysis",
    }
)
UTILITY_COMPONENTS = frozenset({"crypto_alpha", "research_reports"})
OFFLINE_COMPONENTS = frozenset({"jue_codex_lab"})
OFFLINE_MEMORY_OPERATIONS = frozenset(
    {"monthly_review", "policy_revision", "weekly_review"}
)


@dataclass(frozen=True, slots=True)
class LLMModelPolicy:
    tier: str
    model: str
    reasoning_effort: str


def _value(settings: Any, field: str, fallback: str) -> str:
    value = str(getattr(settings, field, fallback) or "").strip()
    return value or fallback


def _tier_for(component: str, operation: str) -> str:
    if component in OFFLINE_COMPONENTS:
        return "offline"
    if component == "investment_memory" and operation in OFFLINE_MEMORY_OPERATIONS:
        return "offline"
    if component in CRITICAL_COMPONENTS:
        return "critical"
    if component in UTILITY_COMPONENTS:
        return "utility"
    return "reasoning"


def resolve_llm_model_policy(
    settings: Any,
    *,
    component: str,
    operation: str = "",
) -> LLMModelPolicy:
    normalized_component = str(component or "").strip().lower()
    normalized_operation = str(operation or "").strip().lower()
    tier = _tier_for(normalized_component, normalized_operation)

    if normalized_component == "binance_block_manager":
        return LLMModelPolicy(
            tier=tier,
            model=_value(
                settings,
                "binance_block_trader_llm_model",
                "gpt-5.6-sol",
            ),
            reasoning_effort=_value(
                settings,
                "binance_block_trader_llm_reasoning_effort",
                "xhigh",
            ),
        )
    if normalized_component == "crypto_market_research":
        return LLMModelPolicy(
            tier=tier,
            model=_value(
                settings,
                "crypto_market_research_llm_model",
                "gpt-5.6-terra",
            ),
            reasoning_effort=_value(
                settings,
                "crypto_market_research_llm_reasoning_effort",
                "high",
            ),
        )
    if normalized_component == "crypto_alpha":
        return LLMModelPolicy(
            tier=tier,
            model=_value(settings, "crypto_alpha_llm_model", "gpt-5.6-luna"),
            reasoning_effort=_value(
                settings,
                "crypto_alpha_llm_reasoning_effort",
                "medium",
            ),
        )
    if tier == "critical":
        return LLMModelPolicy(
            tier=tier,
            model=_value(settings, "llm_model", "gpt-5.6-sol"),
            reasoning_effort=_value(
                settings,
                "llm_reasoning_effort",
                "xhigh",
            ),
        )
    if tier == "utility":
        return LLMModelPolicy(
            tier=tier,
            model=_value(settings, "llm_utility_model", "gpt-5.6-luna"),
            reasoning_effort=_value(
                settings,
                "llm_utility_model_effort",
                "medium",
            ),
        )
    if tier == "offline":
        return LLMModelPolicy(
            tier=tier,
            model=_value(settings, "llm_offline_model", "gpt-5.6-sol"),
            reasoning_effort=_value(
                settings,
                "llm_offline_model_effort",
                "max",
            ),
        )
    return LLMModelPolicy(
        tier=tier,
        model=_value(settings, "llm_reasoning_model", "gpt-5.6-terra"),
        reasoning_effort=_value(
            settings,
            "llm_reasoning_model_effort",
            "high",
        ),
    )


def llm_operation_model_overrides(
    settings: Any,
    *,
    component: str,
) -> dict[str, tuple[str, str]]:
    normalized_component = str(component or "").strip().lower()
    if normalized_component != "investment_memory":
        return {}
    return {
        operation: (
            policy.model,
            policy.reasoning_effort,
        )
        for operation in sorted(OFFLINE_MEMORY_OPERATIONS)
        if (
            policy := resolve_llm_model_policy(
                settings,
                component=normalized_component,
                operation=operation,
            )
        )
    }


def llm_model_config_kwargs(
    settings: Any,
    *,
    component: str,
) -> dict[str, Any]:
    policy = resolve_llm_model_policy(settings, component=component)
    return {
        "model": policy.model,
        "reasoning_effort": policy.reasoning_effort,
        "operation_model_overrides": llm_operation_model_overrides(
            settings,
            component=component,
        ),
    }
