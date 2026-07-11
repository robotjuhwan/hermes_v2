from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ManagerRunTelemetryV1:
    venue: str
    context_generation_ms: float
    prompt_chars: int
    llm_latency_ms: float
    raw_prompt_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    action_count: int = 0
    result_status: str = ""
    fill_provenance: dict[str, int] = field(default_factory=dict)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    wiki_read_mode: str = "shadow"
    wiki_snapshot_id: str = ""
    wiki_coverage_status: str = ""
    wiki_context_chars: int = 0
    wiki_shadow_comparison_id: str = ""
    wiki_shadow_recording_id: str = ""
    wiki_suppressed_new_risk_count: int = 0
    version: str = "manager_run_telemetry_v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        raw_chars = max(int(self.raw_prompt_chars), 0)
        final_chars = max(int(self.prompt_chars), 0)
        payload["prompt_reduction_pct"] = round(
            max(raw_chars - final_chars, 0) / raw_chars * 100.0,
            3,
        ) if raw_chars > 0 else 0.0
        return payload


def manager_action_count(actions: dict[str, Any]) -> int:
    if not isinstance(actions, dict):
        return 0
    return sum(
        len(value)
        for value in actions.values()
        if isinstance(value, list)
    )


def _provenance(row: dict[str, Any]) -> str:
    for key in (
        "fill_provenance",
        "execution_source",
        "fill_source",
        "provenance",
    ):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def build_fill_provenance_summary(
    *,
    actions: dict[str, Any] | None,
    applied: dict[str, Any] | None = None,
) -> dict[str, int]:
    action_payload = actions if isinstance(actions, dict) else {}
    applied_payload = applied if isinstance(applied, dict) else {}
    created_rows = (
        list(applied_payload.get("created") or [])
        if isinstance(applied_payload.get("created"), list)
        else list(action_payload.get("create_blocks") or [])
    )
    paper_fill_count = 0
    exchange_fill_count = 0
    failed_applied_count = 0
    for row in created_rows:
        if not isinstance(row, dict):
            continue
        provenance = _provenance(row)
        status = str(row.get("status") or "").strip().lower()
        if "paper" in provenance:
            paper_fill_count += 1
        elif "exchange" in provenance or provenance in {"live_fill", "filled"}:
            exchange_fill_count += 1
        if status in {"error", "failed", "rejected", "not_filled"}:
            failed_applied_count += 1
    rejected_count = sum(
        len(action_payload.get(key) or [])
        for key in ("rejected_create_blocks", "failed_entries", "rejected_entries")
        if isinstance(action_payload.get(key), list)
    )
    kis_adoption_count = len(action_payload.get("adopt_existing_blocks") or [])
    wallet_adoption_count = sum(
        len(action_payload.get(key) or [])
        for key in ("adopt_wallet_blocks", "adopt_wallet_positions")
        if isinstance(action_payload.get(key), list)
    )
    return {
        "jue_exchange_fill_count": exchange_fill_count,
        "kis_existing_position_adoption_count": kis_adoption_count,
        "binance_wallet_adoption_count": wallet_adoption_count,
        "failed_or_rejected_entry_count": rejected_count + failed_applied_count,
        "paper_fill_count": paper_fill_count,
        "exchange_fill_count": exchange_fill_count,
        "alpha_fill_count": exchange_fill_count,
    }


def build_strategy_authority_gate(
    *,
    fill_proven_sample_count: int,
    attribution_complete: bool,
    net_return_after_cost_pct: float,
    min_samples: int = 30,
) -> dict[str, Any]:
    sample_count = max(int(fill_proven_sample_count), 0)
    required = max(int(min_samples), 1)
    if sample_count <= 0 or not bool(attribution_complete):
        authority = "observe_only"
        reason = "fill_proven_attribution_incomplete"
    elif sample_count < required:
        authority = "restricted"
        reason = "fill_proven_sample_insufficient"
    elif float(net_return_after_cost_pct) <= 0:
        authority = "restricted"
        reason = "net_performance_after_cost_not_positive"
    else:
        authority = "eligible_for_review"
        reason = "fill_proven_sample_and_cost_performance_sufficient"
    return {
        "version": "strategy_authority_gate_v1",
        "authority": authority,
        "reason": reason,
        "fill_proven_sample_count": sample_count,
        "min_samples": required,
        "attribution_complete": bool(attribution_complete),
        "net_return_after_cost_pct": float(net_return_after_cost_pct),
        "automatic_scale_up_allowed": False,
    }
