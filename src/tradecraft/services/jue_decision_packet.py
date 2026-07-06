from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


ACTIVE_BLOCK_STATUSES = {"open", "entry_pending", "exit_pending"}
RULE_EVENT_MARKERS = ("target", "stop", "exit_signal", "entry")
ACTION_KEYS = (
    "create_blocks",
    "adopt_existing_blocks",
    "update_blocks",
    "close_blocks",
    "pause_blocks",
)
APPLIED_KEYS = ("created", "adopted", "updated", "closed_requested", "paused", "rejected")
DECISION_PACKET_VERSION = "decision_packet_v2"
CANONICAL_DECISION_PACKET_PROMPT_VERSION = "canonical_decision_packet_prompt_v1"
DECISION_PACKET_REQUIRED_SECTIONS = (
    "venue_profile",
    "account_pressure",
    "position_pressure",
    "blocks",
    "block_state",
    "quote_regime",
    "recent_rule_events",
    "previous_decision_outcomes",
    "recent_execution_summary",
    "risk_budget",
    "market_pulse",
    "source_coverage",
    "llm_focus_questions",
)
DECISION_PACKET_PROMPT_FIELDS = {
    "canonical_packet": "decision_packet_v2",
    "legacy_packet": "decision_packet",
    "prompt_section": "canonical_decision_packet",
}
DECISION_PACKET_SECTION_CATALOG = {
    "venue_profile": {
        "role": "venue_identity_and_execution_semantics",
        "shape": "object",
        "required": True,
    },
    "account_pressure": {
        "role": "cash_equity_and_deployment_pressure",
        "shape": "object",
        "required": True,
    },
    "position_pressure": {
        "role": "active_exposure_by_symbol_and_horizon",
        "shape": "aggregate_with_breakdowns",
        "required": True,
    },
    "blocks": {
        "role": "normalized_block_rows_with_price_and_stop_policy",
        "shape": "list",
        "required": True,
    },
    "block_state": {
        "role": "block_status_horizon_counts_and_missing_structure",
        "shape": "aggregate",
        "required": True,
    },
    "quote_regime": {
        "role": "latest_quote_quality_and_intraday_context",
        "shape": "aggregate_with_by_symbol",
        "required": True,
    },
    "recent_rule_events": {
        "role": "deterministic_executor_events_since_recent_decisions",
        "shape": "aggregate_with_by_block_and_symbol",
        "required": True,
    },
    "previous_decision_outcomes": {
        "role": "previous_manager_actions_applied_counts_and_errors",
        "shape": "aggregate_with_recent_runs",
        "required": True,
    },
    "recent_execution_summary": {
        "role": "recent_order_exit_signal_and_sell_reason_summary",
        "shape": "aggregate",
        "required": True,
    },
    "risk_budget": {
        "role": "cash_concentration_pending_exit_and_data_risk_flags",
        "shape": "object",
        "required": True,
    },
    "market_pulse": {
        "role": "compact_market_regime_context",
        "shape": "object",
        "required": True,
    },
    "source_coverage": {
        "role": "source_freshness_and_gap_map",
        "shape": "aggregate_with_by_source",
        "required": True,
    },
    "llm_focus_questions": {
        "role": "decision_questions_derived_from_packet_gaps_and_risk",
        "shape": "list",
        "required": True,
    },
}


@dataclass(frozen=True)
class Exposure:
    block_count: int = 0
    exposure: float = 0.0

    def add(self, value: float | None) -> "Exposure":
        return Exposure(
            block_count=self.block_count + 1,
            exposure=self.exposure + (value or 0.0),
        )

    def as_dict(self, total_equity: float | None) -> dict[str, Any]:
        return {
            "block_count": self.block_count,
            "exposure": _compact_number(self.exposure),
            "weight": _ratio(self.exposure, total_equity),
        }


def decision_packet_contract(*, target_scope: str = "shared") -> dict[str, Any]:
    return {
        "version": DECISION_PACKET_VERSION,
        "target_scope": _clean_text(target_scope or "shared", limit=40),
        "producer": "tradecraft.services.jue_decision_packet.build_decision_packet",
        "required_sections": list(DECISION_PACKET_REQUIRED_SECTIONS),
        "section_catalog": decision_packet_section_catalog(),
        "venue_profiles": decision_packet_venue_profiles(),
        "prompt_fields": dict(DECISION_PACKET_PROMPT_FIELDS),
        "consumer_policy": (
            "KIS and Binance managers must treat this packet as the canonical "
            "decision input summary before adding venue-specific prompt sections."
        ),
    }


def decision_packet_section_catalog() -> dict[str, dict[str, Any]]:
    return {
        section: dict(DECISION_PACKET_SECTION_CATALOG[section])
        for section in DECISION_PACKET_REQUIRED_SECTIONS
    }


def decision_packet_venue_profiles() -> dict[str, dict[str, Any]]:
    return {
        "shared": _build_venue_profile("shared"),
        "kis": _build_venue_profile("kis"),
        "binance": _build_venue_profile("binance"),
    }


def validate_decision_packet_contract(
    packet: dict[str, Any] | None,
    *,
    expected_target_scope: str | None = None,
) -> dict[str, Any]:
    row = packet if isinstance(packet, dict) else {}
    schema = row.get("schema") if isinstance(row.get("schema"), dict) else {}
    version = str(row.get("version") or schema.get("version") or "")
    target_scope = _clean_text(
        schema.get("target_scope") or row.get("target_scope") or "shared",
        limit=40,
    )
    expected_scope = _clean_text(
        expected_target_scope or target_scope or "shared",
        limit=40,
    )
    required_sections = [
        str(section)
        for section in list(
            schema.get("required_sections") or DECISION_PACKET_REQUIRED_SECTIONS
        )
        if str(section)
    ]
    present_sections = [section for section in required_sections if section in row]
    missing_sections = [
        section for section in required_sections if section not in present_sections
    ]
    venue_profile = (
        row.get("venue_profile") if isinstance(row.get("venue_profile"), dict) else {}
    )
    venue_scope = _clean_text(
        venue_profile.get("target_scope") or target_scope or "shared",
        limit=40,
    )
    scope_mismatch = bool(
        expected_scope != target_scope or venue_scope != target_scope
    )
    version_mismatch = version != DECISION_PACKET_VERSION
    status = (
        "invalid"
        if missing_sections or scope_mismatch or version_mismatch
        else "ok"
    )
    return {
        "status": status,
        "version": version,
        "expected_version": DECISION_PACKET_VERSION,
        "version_mismatch": version_mismatch,
        "target_scope": target_scope,
        "expected_target_scope": expected_scope,
        "venue_profile_target_scope": venue_scope,
        "scope_mismatch": scope_mismatch,
        "required_sections": required_sections,
        "present_required_sections": present_sections,
        "missing_required_sections": missing_sections,
    }


def build_canonical_decision_packet_prompt_section(
    *,
    target_scope: str,
    decision_packet_v2: dict[str, Any] | None,
    legacy_decision_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = decision_packet_v2 if isinstance(decision_packet_v2, dict) else {}
    legacy = legacy_decision_packet if isinstance(legacy_decision_packet, dict) else {}
    schema = packet.get("schema") if isinstance(packet.get("schema"), dict) else {}
    required_sections = [
        str(row)
        for row in list(schema.get("required_sections") or DECISION_PACKET_REQUIRED_SECTIONS)
        if str(row)
    ]
    present_sections = [section for section in required_sections if section in packet]
    prompt_scope = (
        schema.get("target_scope")
        or packet.get("target_scope")
        or legacy.get("target_scope")
        or target_scope
        or "shared"
    )
    schema_version = str(packet.get("version") or schema.get("version") or DECISION_PACKET_VERSION)
    raw_section_catalog = schema.get("section_catalog")
    if isinstance(raw_section_catalog, dict):
        section_catalog = {
            section: (
                dict(raw_section_catalog.get(section))
                if isinstance(raw_section_catalog.get(section), dict)
                else dict(DECISION_PACKET_SECTION_CATALOG.get(section, {}))
            )
            for section in required_sections
        }
    else:
        section_catalog = {
            section: dict(DECISION_PACKET_SECTION_CATALOG.get(section, {}))
            for section in required_sections
        }
    prompt_fields = (
        dict(schema.get("prompt_fields"))
        if isinstance(schema.get("prompt_fields"), dict)
        else dict(DECISION_PACKET_PROMPT_FIELDS)
    )
    target_scope_text = _clean_text(prompt_scope, limit=40)
    contract_validation = validate_decision_packet_contract(
        packet,
        expected_target_scope=target_scope_text,
    )
    decision_warnings: list[str] = []
    if contract_validation["status"] != "ok":
        decision_warnings.append("decision_packet_contract_invalid")
    if contract_validation.get("version_mismatch"):
        decision_warnings.append("decision_packet_version_mismatch")
    if contract_validation.get("scope_mismatch"):
        decision_warnings.append("decision_packet_scope_mismatch")
    if contract_validation.get("missing_required_sections"):
        decision_warnings.append("decision_packet_missing_sections")
    legacy_context_mode = "policy_metadata_only" if legacy else "absent"
    packet_identity = {
        "version": CANONICAL_DECISION_PACKET_PROMPT_VERSION,
        "schema_version": schema_version,
        "target_scope": target_scope_text,
        "producer": _clean_text(
            schema.get("producer")
            or "tradecraft.services.jue_decision_packet.build_decision_packet",
            limit=160,
        ),
        "primary_prompt_key": "decision_packet_v2",
        "legacy_prompt_key": "decision_packet",
        "required_section_count": len(required_sections),
        "present_section_count": len(present_sections),
        "legacy_context_mode": legacy_context_mode,
    }
    return {
        "version": CANONICAL_DECISION_PACKET_PROMPT_VERSION,
        "target_scope": target_scope_text,
        "schema_version": schema_version,
        "packet_identity": packet_identity,
        "primary_prompt_key": "decision_packet_v2",
        "legacy_prompt_key": "decision_packet",
        "prompt_fields": prompt_fields,
        "required_sections": required_sections,
        "section_catalog": section_catalog,
        "present_sections": present_sections,
        "missing_required_sections": [
            section for section in required_sections if section not in packet
        ],
        "contract_validation": contract_validation,
        "decision_packet_status": contract_validation["status"],
        "decision_warnings": decision_warnings,
        "legacy_context_mode": legacy_context_mode,
        "consumer_policy": (
            "Use decision_packet_v2 as the canonical normalized decision input. "
            "Use decision_packet only for legacy policy metadata and never as a "
            "separate source of truth when it conflicts with decision_packet_v2."
        ),
    }


def build_canonical_decision_prompt_bundle(
    *,
    target_scope: str,
    decision_packet_v2: dict[str, Any] | None,
    legacy_decision_packet: dict[str, Any] | None = None,
    base_inputs: list[str] | tuple[str, ...] | None = None,
    lifecycle_packet_key: str = "",
    extra_inputs: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    canonical_section = build_canonical_decision_packet_prompt_section(
        target_scope=target_scope,
        decision_packet_v2=decision_packet_v2,
        legacy_decision_packet=legacy_decision_packet,
    )
    canonical_keys = ["canonical_decision_packet", "decision_packet_v2"]
    lifecycle_key = _clean_text(lifecycle_packet_key, limit=80)
    if lifecycle_key:
        canonical_keys.append(lifecycle_key)
    canonical_keys.append("decision_packet")
    inputs = _dedupe_prompt_inputs(
        [
            *(base_inputs or []),
            *canonical_keys,
            *(extra_inputs or []),
        ]
    )
    return {
        "canonical_decision_packet": canonical_section,
        "decision_packet_policy": _decision_packet_policy_text(target_scope),
        "decision_inputs": inputs,
    }


def _decision_packet_policy_text(target_scope: str) -> str:
    scope = _clean_text(target_scope or "shared", limit=40).lower()
    policy = (
        "decision_packet_v2 is the canonical normalized decision context. "
        "Use decision_packet only for legacy policy metadata and never as a "
        "separate source of truth when it conflicts with decision_packet_v2."
    )
    if scope == "kis":
        return (
            f"{policy} Crypto-specific policies are not KIS asset evidence and "
            "must not be treated as active KIS trading policies."
        )
    return policy


def _dedupe_prompt_inputs(values: list[str] | tuple[str, ...]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean_text(value, limit=80)
        if not item or item in seen:
            continue
        rows.append(item)
        seen.add(item)
    return rows


def build_decision_packet(
    account: dict[str, Any] | None,
    blocks: list[dict[str, Any]] | None,
    quotes: dict[str, Any] | list[dict[str, Any]] | None,
    recent_events: list[dict[str, Any]] | None,
    previous_manager_runs: list[dict[str, Any]] | dict[str, Any] | None,
    market_pulse: dict[str, Any] | None = None,
    target_scope: str = "shared",
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account_row = account if isinstance(account, dict) else {}
    block_rows = [row for row in list(blocks or []) if isinstance(row, dict)]
    quote_rows = _normalize_quotes(quotes)
    event_rows = [row for row in list(recent_events or []) if isinstance(row, dict)]
    manager_runs = _normalize_manager_runs(previous_manager_runs)
    pulse = market_pulse if isinstance(market_pulse, dict) else {}
    venue_profile = _build_venue_profile(target_scope)

    account_pressure = _build_account_pressure(account_row)
    position_pressure = _build_position_pressure(
        block_rows,
        quote_rows,
        account_pressure["total_equity"],
    )
    block_state = _build_block_state(block_rows)
    quote_regime = _build_quote_regime(quote_rows)
    recent_rule_events = _build_recent_rule_events(event_rows, block_rows)
    previous_decision_outcomes = _build_previous_decision_outcomes(manager_runs)
    recent_execution_summary = _build_recent_execution_summary(event_rows)
    source_coverage = _build_source_coverage(source_context)
    risk_budget = _build_risk_budget(
        account_pressure=account_pressure,
        position_pressure=position_pressure,
        block_state=block_state,
        quote_regime=quote_regime,
    )

    packet = {
        "version": DECISION_PACKET_VERSION,
        "schema": decision_packet_contract(target_scope=target_scope),
        "generated_at": _deterministic_generated_at(
            account_row,
            pulse,
            event_rows,
            manager_runs,
            block_rows,
        ),
        "venue_profile": venue_profile,
        "account_pressure": account_pressure,
        "position_pressure": position_pressure,
        "blocks": _build_block_packets(block_rows, quote_rows, event_rows),
        "block_state": block_state,
        "quote_regime": quote_regime,
        "recent_rule_events": recent_rule_events,
        "previous_decision_outcomes": previous_decision_outcomes,
        "previous_decision_reviews": previous_decision_outcomes.get("recent_runs", []),
        "recent_execution_summary": recent_execution_summary,
        "risk_budget": risk_budget,
        "market_pulse": _public_market_pulse(pulse),
        "source_coverage": source_coverage,
        "llm_focus_questions": _build_focus_questions(
            account_pressure=account_pressure,
            position_pressure=position_pressure,
            block_state=block_state,
            quote_regime=quote_regime,
            recent_rule_events=recent_rule_events,
            previous_decision_outcomes=previous_decision_outcomes,
            source_coverage=source_coverage,
        ),
    }
    packet["contract_validation"] = validate_decision_packet_contract(
        packet,
        expected_target_scope=target_scope,
    )
    return packet


def _build_venue_profile(target_scope: str) -> dict[str, Any]:
    scope = _clean_text(target_scope or "shared", limit=40).lower() or "shared"
    if scope == "kis":
        return {
            "target_scope": "kis",
            "venue": "kis",
            "asset_class": "kr_equity",
            "base_currency": "KRW",
            "price_unit": "KRW",
            "quantity_unit": "shares",
            "trading_session": "krx_regular",
            "supports_short": False,
        }
    if scope == "binance":
        return {
            "target_scope": "binance",
            "venue": "binance",
            "asset_class": "crypto",
            "base_currency": "USDT",
            "price_unit": "USDT",
            "quantity_unit": "asset_units",
            "trading_session": "24h",
            "supports_short": True,
        }
    return {
        "target_scope": scope,
        "venue": scope,
        "asset_class": "multi_asset",
        "base_currency": "mixed",
        "price_unit": "mixed",
        "quantity_unit": "venue_native",
        "trading_session": "mixed",
        "supports_short": None,
    }


def build_decision_lifecycle_packet(
    *,
    stage: str,
    workflow_id: str,
    artifacts: list[dict[str, Any]] | None = None,
    max_artifacts: int = 12,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in list(artifacts or [])[: max(max_artifacts, 1)]:
        if not isinstance(row, dict):
            continue
        artifact_id = _clean_text(row.get("artifact_id"), limit=120)
        symbol = _clean_text(row.get("symbol"), limit=40)
        evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
        implications = (
            row.get("block_implications")
            if isinstance(row.get("block_implications"), list)
            else []
        )
        if not symbol:
            rejected.append({"artifact_id": artifact_id, "reason": "missing_symbol"})
            continue
        if not evidence:
            rejected.append(
                {
                    "artifact_id": artifact_id,
                    "symbol": symbol,
                    "reason": "missing_evidence",
                }
            )
            continue
        accepted.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": _clean_text(
                    row.get("artifact_type") or stage,
                    limit=80,
                ),
                "symbol": symbol,
                "workflow_id": _clean_text(
                    row.get("workflow_id") or workflow_id,
                    limit=120,
                ),
                "thesis": row.get("thesis") if isinstance(row.get("thesis"), dict) else {},
                "valuation": (
                    row.get("valuation")
                    if isinstance(row.get("valuation"), dict)
                    else {}
                ),
                "catalysts": [
                    item
                    for item in list(row.get("catalysts") or [])[:6]
                    if isinstance(item, (dict, str))
                ],
                "sector_context": (
                    row.get("sector_context")
                    if isinstance(row.get("sector_context"), dict)
                    else {}
                ),
                "portfolio_context": (
                    row.get("portfolio_context")
                    if isinstance(row.get("portfolio_context"), dict)
                    else {}
                ),
                "evidence": [
                    item for item in evidence[:8] if isinstance(item, (dict, str))
                ],
                "block_implications": [
                    item for item in implications[:6] if isinstance(item, dict)
                ],
                "rejected_actions": [
                    item
                    for item in list(row.get("rejected_actions") or [])[:6]
                    if isinstance(item, (dict, str))
                ],
            }
        )
    symbols = sorted({row["symbol"] for row in accepted if row.get("symbol")})
    return {
        "version": "decision_lifecycle_v3",
        "stage": _clean_text(stage, limit=80),
        "workflow_id": _clean_text(workflow_id, limit=120),
        "artifact_count": len(accepted),
        "symbols": symbols,
        "artifacts": accepted,
        "block_implications": [
            {**item, "symbol": row["symbol"], "artifact_id": row["artifact_id"]}
            for row in accepted
            for item in row.get("block_implications", [])
            if isinstance(item, dict)
        ][:20],
        "rejected_artifacts": rejected[:20],
    }


def _build_block_packets(
    blocks: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in blocks:
        symbol = _symbol_from(block)
        technical = _technical_features(quotes.get(symbol, {}))
        rows.append(
            {
                "block_id": str(block.get("block_id") or ""),
                "symbol": symbol,
                "name": str(block.get("name") or symbol),
                "status": str(block.get("status") or ""),
                "horizon": _horizon_from(block),
                "qty_open": _compact_number(_first_number(block, ("qty_open", "qty", "qty_initial"))),
                "entry_price": _compact_number(_first_number(block, ("entry_price", "avg_price"))),
                "target_price": _compact_number(_to_number(block.get("target_price"))),
                "stop_price": _compact_number(_to_number(block.get("stop_price"))),
                "technical": technical,
                "stop_policy": _stop_policy(block, technical, events),
            }
        )
    return rows


def _build_account_pressure(account: dict[str, Any]) -> dict[str, Any]:
    cash = _first_number(
        account,
        ("cash", "cash_krw", "available_cash", "dnca_tot_amt", "available_krw"),
    )
    total_equity = _first_number(
        account,
        ("total_equity", "equity", "total_assets", "total_value", "tot_evlu_amt"),
    )
    if total_equity is None:
        total_equity = _infer_total_equity(account, cash)
    deployed = None
    if cash is not None and total_equity is not None:
        deployed = max(total_equity - cash, 0.0)
    return {
        "cash": _compact_number(cash),
        "total_equity": _compact_number(total_equity),
        "deployed_value": _compact_number(deployed),
        "cash_ratio": _ratio(cash, total_equity),
        "deployment_ratio": _ratio(deployed, total_equity),
        "cash_pressure": _cash_pressure(cash, total_equity),
    }


def _build_position_pressure(
    blocks: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    total_equity: float | None,
) -> dict[str, Any]:
    by_symbol: dict[str, Exposure] = {}
    by_horizon: dict[str, Exposure] = {}
    total_active_exposure = 0.0
    for block in blocks:
        status = str(block.get("status") or "").strip()
        if status not in ACTIVE_BLOCK_STATUSES:
            continue
        symbol = _symbol_from(block)
        horizon = _horizon_from(block)
        exposure = _block_exposure(block, quotes.get(symbol, {}))
        by_symbol[symbol] = by_symbol.get(symbol, Exposure()).add(exposure)
        by_horizon[horizon] = by_horizon.get(horizon, Exposure()).add(exposure)
        total_active_exposure += exposure or 0.0

    symbol_payload = {
        key: by_symbol[key].as_dict(total_equity)
        for key in sorted(by_symbol)
        if key
    }
    horizon_payload = {
        key: by_horizon[key].as_dict(total_equity)
        for key in sorted(by_horizon)
        if key
    }
    max_symbol = _max_exposure_key(symbol_payload)
    core_etf_exposure = horizon_payload.get("core_etf", {}).get("exposure")
    return {
        "active_block_count": sum(item["block_count"] for item in symbol_payload.values()),
        "total_active_exposure": _compact_number(total_active_exposure),
        "by_symbol": symbol_payload,
        "by_horizon": horizon_payload,
        "max_symbol": max_symbol,
        "max_symbol_weight": (
            symbol_payload.get(max_symbol, {}).get("weight") if max_symbol else None
        ),
        "active_core_etf_exposure": core_etf_exposure,
        "active_core_etf_weight": _ratio(_to_number(core_etf_exposure), total_equity),
    }


def _build_block_state(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_horizon: dict[str, int] = {}
    by_status_horizon: dict[str, dict[str, int]] = {}
    missing_target_or_stop: list[dict[str, str | None]] = []
    for block in blocks:
        status = str(block.get("status") or "unknown").strip() or "unknown"
        horizon = _horizon_from(block)
        by_status[status] = by_status.get(status, 0) + 1
        by_horizon[horizon] = by_horizon.get(horizon, 0) + 1
        by_status_horizon.setdefault(status, {})
        by_status_horizon[status][horizon] = by_status_horizon[status].get(horizon, 0) + 1
        if status in ACTIVE_BLOCK_STATUSES and (
            _to_number(block.get("target_price")) is None
            or _to_number(block.get("stop_price")) is None
        ):
            missing_target_or_stop.append(
                {
                    "block_id": str(block.get("block_id") or ""),
                    "symbol": _symbol_from(block),
                    "horizon": horizon,
                }
            )
    return {
        "total_blocks": len(blocks),
        "counts_by_status": dict(sorted(by_status.items())),
        "counts_by_horizon": dict(sorted(by_horizon.items())),
        "counts_by_status_horizon": {
            status: dict(sorted(rows.items()))
            for status, rows in sorted(by_status_horizon.items())
        },
        "active_statuses": sorted(ACTIVE_BLOCK_STATUSES),
        "missing_target_or_stop": missing_target_or_stop[:20],
        "missing_target_or_stop_count": len(missing_target_or_stop),
    }


def _build_quote_regime(quotes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_symbol: dict[str, dict[str, Any]] = {}
    stale_or_error_count = 0
    for symbol in sorted(quotes):
        quote = quotes[symbol]
        raw = _raw_dict(quote)
        price = _first_number(quote, ("price", "last", "close", "current_price"))
        high = _first_number(quote, ("high", "day_high", "stck_hgpr"))
        low = _first_number(quote, ("low", "day_low", "stck_lwpr"))
        if high is None:
            high = _first_number(raw, ("stck_hgpr", "high", "dayHigh"))
        if low is None:
            low = _first_number(raw, ("stck_lwpr", "low", "dayLow"))
        day_change_pct = _first_number(
            quote,
            ("day_change_pct", "change_pct", "prdy_ctrt", "stck_prdy_ctrt"),
        )
        if day_change_pct is None:
            day_change_pct = _first_number(
                raw,
                ("stck_prdy_ctrt", "prdy_ctrt", "fluctuationsRatio", "changeRate"),
            )
        volume = _first_number(quote, ("volume", "acml_vol", "accumulated_volume"))
        if volume is None:
            volume = _first_number(raw, ("acml_vol", "volume", "accumulatedTradingVolume"))
        value_proxy = _first_number(
            quote,
            ("value_proxy", "trading_value", "acml_tr_pbmn", "accumulated_value"),
        )
        if value_proxy is None:
            value_proxy = _first_number(
                raw,
                ("acml_tr_pbmn", "tradingValue", "accumulatedTradingValue"),
            )
        has_error = _has_quote_error(quote)
        is_stale = _has_quote_stale_flag(quote)
        if has_error or is_stale:
            stale_or_error_count += 1
        by_symbol[symbol] = {
            "symbol": symbol,
            "price": _compact_number(price),
            "day_change_pct": _compact_number(day_change_pct),
            "intraday_range_pct": _ratio(
                (high - low) if high is not None and low is not None else None,
                price,
                scale=100.0,
            ),
            "volume": _compact_number(volume),
            "value_proxy": _compact_number(value_proxy),
            "is_stale": is_stale,
            "has_error": has_error,
            "error_message": str(quote.get("error_message") or quote.get("error") or ""),
            "source": str(quote.get("source") or ""),
            "fetched_at": str(quote.get("fetched_at") or quote.get("as_of") or ""),
        }
    return {
        "symbol_count": len(by_symbol),
        "stale_or_error_count": stale_or_error_count,
        "by_symbol": by_symbol,
    }


def _build_source_coverage(source_context: dict[str, Any] | None) -> dict[str, Any]:
    context = source_context if isinstance(source_context, dict) else {}
    by_source: dict[str, dict[str, Any]] = {}
    ok_count = 0
    warning_count = 0
    error_count = 0
    for source_id in sorted(str(key) for key in context if str(key).strip()):
        raw = context.get(source_id)
        if not isinstance(raw, dict) or not raw:
            continue
        status = _normalized_source_status(raw.get("status"))
        if status == "ok":
            ok_count += 1
        elif status == "error":
            error_count += 1
        else:
            warning_count += 1
        by_source[source_id] = {
            "source_id": source_id,
            "status": status,
            "item_count": _source_count(raw, ("item_count", "items_count", "count")),
            "symbol_count": _source_count(
                raw,
                ("symbol_count", "symbols_count", "candidate_count", "signal_count"),
            ),
            "age_minutes": _compact_number(
                _first_number(raw, ("age_minutes", "stale_minutes", "minutes_old"))
            ),
            "as_of": str(
                raw.get("as_of")
                or raw.get("generated_at")
                or raw.get("updated_at")
                or raw.get("fetched_at")
                or ""
            ),
            "error_message": _clean_text(
                raw.get("error_message") or raw.get("error"),
                limit=180,
            ),
        }
    return {
        "source_count": len(by_source),
        "ok_count": ok_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "by_source": by_source,
    }


def _normalized_source_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"ok", "ready", "active", "success", "fresh", "healthy"}:
        return "ok"
    if raw in {"error", "failed", "fail", "unavailable", "exception"}:
        return "error"
    if raw in {"stale", "warning", "warn", "degraded", "missing", "empty", "disabled"}:
        return "warning"
    return raw or "unknown"


def _source_count(source: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    value = _first_number(source, keys)
    if value is not None:
        return int(value)
    for key in ("items", "candidates", "signals", "symbols"):
        row = source.get(key)
        if isinstance(row, (list, tuple, set)):
            return len(row)
        if isinstance(row, dict):
            return len(row)
    return None


def _technical_features(quote: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_dict(quote)
    price = _first_number(quote, ("price", "last", "close", "current_price"))
    if price is None:
        price = _first_number(raw, ("stck_prpr", "price", "last"))
    open_price = _first_number(quote, ("open", "open_price", "stck_oprc"))
    if open_price is None:
        open_price = _first_number(raw, ("stck_oprc", "open", "openPrice"))
    high = _first_number(quote, ("high", "day_high", "stck_hgpr"))
    if high is None:
        high = _first_number(raw, ("stck_hgpr", "high", "dayHigh"))
    low = _first_number(quote, ("low", "day_low", "stck_lwpr"))
    if low is None:
        low = _first_number(raw, ("stck_lwpr", "low", "dayLow"))
    day_change_pct = _first_number(quote, ("day_change_pct", "change_pct", "prdy_ctrt"))
    if day_change_pct is None:
        day_change_pct = _first_number(raw, ("stck_prdy_ctrt", "prdy_ctrt", "changeRate"))
    volume = _first_number(quote, ("volume", "acml_vol", "accumulated_volume"))
    if volume is None:
        volume = _first_number(raw, ("acml_vol", "volume", "accumulatedTradingVolume"))
    value_proxy = _first_number(quote, ("value_proxy", "trading_value", "acml_tr_pbmn"))
    if value_proxy is None:
        value_proxy = _first_number(raw, ("acml_tr_pbmn", "tradingValue", "accumulatedTradingValue"))
    program_net_qty = _first_number(quote, ("program_net_qty", "pgtr_ntby_qty"))
    if program_net_qty is None:
        program_net_qty = _first_number(raw, ("pgtr_ntby_qty", "programNetQuantity"))
    intraday_position_pct = None
    if price is not None and high is not None and low is not None and high > low:
        intraday_position_pct = round((price - low) / (high - low) * 100, 2)
    drawdown_from_high_pct = None
    if price is not None and high:
        drawdown_from_high_pct = round((price - high) / high * 100, 2)
    rebound_from_low_pct = None
    if price is not None and low:
        rebound_from_low_pct = round((price - low) / low * 100, 2)
    return {
        "price": _compact_number(price),
        "open": _compact_number(open_price),
        "high": _compact_number(high),
        "low": _compact_number(low),
        "day_change_pct": _compact_number(day_change_pct),
        "volume": _compact_number(volume),
        "value_proxy": _compact_number(value_proxy),
        "program_net_qty": _compact_number(program_net_qty),
        "intraday_position_pct": intraday_position_pct,
        "drawdown_from_high_pct": drawdown_from_high_pct,
        "rebound_from_low_pct": rebound_from_low_pct,
        "is_stale": _has_quote_stale_flag(quote),
        "has_error": _has_quote_error(quote),
        "source": str(quote.get("source") or ""),
        "fetched_at": str(quote.get("fetched_at") or quote.get("as_of") or ""),
    }


def _stop_policy(
    block: dict[str, Any],
    technical: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    horizon = _horizon_from(block)
    price = _to_number(technical.get("price"))
    stop = _to_number(block.get("stop_price"))
    target = _to_number(block.get("target_price"))
    return {
        "horizon": horizon,
        "stop_price": _compact_number(stop),
        "target_price": _compact_number(target),
        "stop_touched_now": bool(price is not None and stop is not None and price <= stop),
        "target_touched_now": bool(price is not None and target is not None and price >= target),
        "touch_action": "rule_exit" if horizon == "short" else "manager_review",
        "latest_signal": _latest_signal_for_block(str(block.get("block_id") or ""), events),
        "instruction": (
            "Short blocks may exit by rule. Mid, long, and core ETF blocks require "
            "manager review after target/stop touch."
        ),
    }


def _latest_signal_for_block(block_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for event in events:
        payload = _payload_dict(event)
        if str(event.get("block_id") or payload.get("block_id") or "") != block_id:
            continue
        if str(event.get("event_type") or "") != "exit_signal":
            continue
        matches.append(
            {
                "reason": str(payload.get("reason") or ""),
                "price": _compact_number(_to_number(payload.get("price"))),
                "created_at": str(event.get("created_at") or ""),
            }
        )
    matches.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return matches[0] if matches else {}


def _build_recent_rule_events(
    events: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    block_symbols = {str(row.get("block_id") or ""): _symbol_from(row) for row in blocks}
    by_block: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    event_count = 0
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        if not _is_rule_event(event_type):
            continue
        payload = _payload_dict(event)
        block_id = str(event.get("block_id") or payload.get("block_id") or "")
        symbol = str(
            event.get("symbol")
            or payload.get("symbol")
            or block_symbols.get(block_id)
            or ""
        ).strip()
        summary = {
            "event_type": event_type,
            "block_id": block_id,
            "symbol": symbol,
            "message": _clean_text(event.get("message"), limit=160),
            "created_at": str(event.get("created_at") or event.get("run_at") or ""),
        }
        if block_id:
            group = by_block.setdefault(
                block_id,
                {"symbol": symbol, "event_count": 0, "event_types": [], "events": []},
            )
            group["event_count"] += 1
            group["event_types"].append(event_type)
            group["events"].append(summary)
        if symbol:
            group = by_symbol.setdefault(
                symbol,
                {"event_count": 0, "event_types": [], "events": []},
            )
            group["event_count"] += 1
            group["event_types"].append(event_type)
            group["events"].append(summary)
        event_count += 1
    return {
        "event_count": event_count,
        "by_block": by_block,
        "by_symbol": by_symbol,
    }


def _build_recent_execution_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    sell_reasons: dict[str, int] = {}
    exit_signals: dict[str, int] = {}
    order_sides: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        payload = _payload_dict(event)
        if event_type == "order":
            side = str(payload.get("side") or "").strip().lower() or "unknown"
            order_sides[side] = order_sides.get(side, 0) + 1
            if side == "sell":
                reason = str(payload.get("reason") or "unknown")
                sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        if event_type == "exit_signal":
            reason = str(payload.get("reason") or "unknown")
            exit_signals[reason] = exit_signals.get(reason, 0) + 1
    return {
        "order_sides": dict(sorted(order_sides.items())),
        "sell_reasons": dict(sorted(sell_reasons.items())),
        "exit_signals": dict(sorted(exit_signals.items())),
    }


def _build_previous_decision_outcomes(runs: list[dict[str, Any]]) -> dict[str, Any]:
    action_totals = {key: 0 for key in ACTION_KEYS}
    applied_totals = {key: 0 for key in APPLIED_KEYS}
    compact_runs: list[dict[str, Any]] = []
    last_error = ""
    for run in runs:
        actions = run.get("actions") if isinstance(run.get("actions"), dict) else {}
        applied = run.get("applied") if isinstance(run.get("applied"), dict) else {}
        run_actions: dict[str, int] = {}
        run_applied: dict[str, int] = {}
        for key in ACTION_KEYS:
            count = len(_as_list(actions.get(key)))
            action_totals[key] += count
            run_actions[key] = count
        for key in APPLIED_KEYS:
            count = len(_as_list(applied.get(key)))
            applied_totals[key] += count
            run_applied[key] = count
        error_message = _clean_text(run.get("error_message"), limit=600)
        if error_message:
            last_error = error_message
        compact_runs.append(
            {
                "run_id": run.get("run_id") or run.get("id"),
                "run_at": str(run.get("run_at") or ""),
                "status": str(run.get("status") or ""),
                "mode": str(run.get("mode") or ""),
                "action_counts": run_actions,
                "applied_counts": run_applied,
                "error_message": error_message,
            }
        )
    return {
        "run_count": len(runs),
        "action_totals": action_totals,
        "applied_totals": applied_totals,
        "last_run": compact_runs[-1] if compact_runs else {},
        "recent_runs": compact_runs[-5:],
        "last_error": last_error,
    }


def _build_risk_budget(
    *,
    account_pressure: dict[str, Any],
    position_pressure: dict[str, Any],
    block_state: dict[str, Any],
    quote_regime: dict[str, Any],
) -> dict[str, Any]:
    pending_exit_count = int(block_state.get("counts_by_status", {}).get("exit_pending", 0))
    max_symbol = str(position_pressure.get("max_symbol") or "")
    max_symbol_weight = _to_number(position_pressure.get("max_symbol_weight"))
    stale_or_error_count = int(quote_regime.get("stale_or_error_count") or 0)
    cash_pressure = str(account_pressure.get("cash_pressure") or "unknown")
    flags: list[str] = []
    if cash_pressure == "tight":
        flags.append("cash_tight")
    if max_symbol_weight is not None and max_symbol_weight >= 0.35:
        flags.append("concentration_high")
    if pending_exit_count:
        flags.append("pending_exits")
    if stale_or_error_count:
        flags.append("stale_or_error_quotes")
    return {
        "cash_pressure": cash_pressure,
        "max_symbol": max_symbol,
        "max_symbol_weight": _compact_number(max_symbol_weight),
        "pending_exit_count": pending_exit_count,
        "stale_or_error_quote_count": stale_or_error_count,
        "missing_target_or_stop_count": int(block_state.get("missing_target_or_stop_count") or 0),
        "risk_flags": flags,
    }


def _build_focus_questions(
    *,
    account_pressure: dict[str, Any],
    position_pressure: dict[str, Any],
    block_state: dict[str, Any],
    quote_regime: dict[str, Any],
    recent_rule_events: dict[str, Any],
    previous_decision_outcomes: dict[str, Any],
    source_coverage: dict[str, Any],
) -> list[str]:
    questions = [
        "Do existing open blocks need target or stop revision before adding exposure?",
        "Is the current ETF/core balance appropriate for the account pressure?",
    ]
    if _to_number(position_pressure.get("max_symbol_weight")) is not None:
        questions.append("Does the largest symbol exposure create concentration risk?")
    if int(quote_regime.get("stale_or_error_count") or 0):
        questions.append("Review symbols with stale or failed quote data before sizing new actions.")
    if int(block_state.get("counts_by_status", {}).get("exit_pending", 0)):
        questions.append("Should pending exits be prioritized before opening or adopting blocks?")
    if str(account_pressure.get("cash_pressure") or "") == "tight":
        questions.append("Is cash pressure too tight for new entries without reducing exposure?")
    if int(recent_rule_events.get("event_count") or 0):
        questions.append("Did recent target, stop, exit, or entry events change the block thesis?")
    if previous_decision_outcomes.get("last_error"):
        questions.append("Did the previous manager error leave any actions unapplied?")
    if int(source_coverage.get("warning_count") or 0) or int(
        source_coverage.get("error_count") or 0
    ):
        questions.append(
            "Treat stale or failed source_coverage entries as explicit decision gaps."
        )
    return questions


def _normalize_quotes(value: dict[str, Any] | list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = []
        for key, row in value.items():
            if isinstance(row, dict):
                item = dict(row)
                item.setdefault("symbol", str(key))
                rows.append(item)
    else:
        rows = []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _symbol_from(row)
        if symbol:
            out[symbol] = row
    return out


def _raw_dict(quote: dict[str, Any]) -> dict[str, Any]:
    raw = quote.get("raw")
    if isinstance(raw, dict):
        return raw
    raw_json = quote.get("raw_json")
    if isinstance(raw_json, dict):
        return raw_json
    if isinstance(raw_json, str):
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _payload_dict(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    payload_json = event.get("payload_json")
    if isinstance(payload_json, dict):
        return payload_json
    if isinstance(payload_json, str):
        try:
            parsed = json.loads(payload_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalize_manager_runs(value: list[dict[str, Any]] | dict[str, Any] | None) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _deterministic_generated_at(
    account: dict[str, Any],
    market_pulse: dict[str, Any],
    events: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> str:
    candidates: list[str] = []
    for source in (account, market_pulse):
        candidates.extend(
            str(source.get(key) or "")
            for key in ("generated_at", "as_of", "fetched_at", "updated_at")
        )
    for rows in (events, runs, blocks):
        for row in rows:
            candidates.extend(
                str(row.get(key) or "")
                for key in ("generated_at", "created_at", "run_at", "updated_at")
            )
    return max([item for item in candidates if item], default="")


def _public_market_pulse(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        return {}
    return {
        "status": value.get("status"),
        "regime": value.get("regime") or value.get("market_regime"),
        "risk_level": value.get("risk_level"),
        "as_of": value.get("as_of") or value.get("generated_at"),
    }


def _infer_total_equity(account: dict[str, Any], cash: float | None) -> float | None:
    total = cash
    found = cash is not None
    for row in _as_list(account.get("positions")):
        if not isinstance(row, dict):
            continue
        value = _first_number(row, ("market_value", "value_krw", "valuation", "exposure"))
        if value is None:
            qty = _first_number(row, ("quantity", "qty", "qty_open"))
            price = _first_number(row, ("price", "mark_price", "avg_price"))
            value = qty * price if qty is not None and price is not None else None
        if value is not None:
            total = (total or 0.0) + value
            found = True
    return total if found else None


def _block_exposure(block: dict[str, Any], quote: dict[str, Any]) -> float | None:
    explicit = _first_number(block, ("exposure", "market_value", "value_krw"))
    if explicit is not None:
        return explicit
    qty = _first_number(block, ("qty_open", "quantity", "qty", "qty_initial"))
    price = _first_number(block, ("entry_price", "avg_price", "mark_price", "price"))
    if price is None:
        price = _first_number(quote, ("price", "last", "close", "current_price"))
    if qty is None or price is None:
        return None
    return qty * price


def _horizon_from(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    raw = row.get("horizon") or metadata.get("horizon") or metadata.get("horizon_bias")
    text = re.sub(r"[\s/-]+", "_", str(raw or "unknown").strip().lower())
    aliases = {
        "core": "core_etf",
        "coreetf": "core_etf",
        "core_etf": "core_etf",
        "etf_core": "core_etf",
        "etf_core_block": "core_etf",
        "etf-core": "core_etf",
        "etf": "core_etf",
        "short_term": "short",
        "mid_term": "mid",
        "medium": "mid",
        "long_term": "long",
    }
    return aliases.get(text, text or "unknown")


def _symbol_from(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or row.get("asset") or "").strip()


def _first_number(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _to_number(source.get(key))
        if value is not None:
            return value
    return None


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("%", "").replace("−", "-").replace("＋", "+")
    text = re.sub(r"^\+", "", text)
    if text.lower() in {"-", "--", "n/a", "na", "none", "null", "nan"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _ratio(
    numerator: float | None,
    denominator: float | None,
    *,
    scale: float = 1.0,
) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round((numerator / denominator) * scale, 4)


def _cash_pressure(cash: float | None, total_equity: float | None) -> str:
    cash_ratio = _ratio(cash, total_equity)
    if cash_ratio is None:
        return "unknown"
    if cash_ratio < 0.10:
        return "tight"
    if cash_ratio > 0.40:
        return "loose"
    return "balanced"


def _compact_number(value: float | None) -> float | int | None:
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return round(float(value), 4)


def _max_exposure_key(rows: dict[str, dict[str, Any]]) -> str:
    if not rows:
        return ""
    return max(rows, key=lambda key: _to_number(rows[key].get("exposure")) or 0.0)


def _has_quote_error(quote: dict[str, Any]) -> bool:
    status = str(quote.get("status") or "").strip().lower()
    return bool(
        status in {"error", "failed", "quote_error"}
        or quote.get("error")
        or quote.get("error_message")
    )


def _has_quote_stale_flag(quote: dict[str, Any]) -> bool:
    status = str(quote.get("status") or "").strip().lower()
    return bool(quote.get("stale") or quote.get("is_stale") or status == "stale")


def _is_rule_event(event_type: str) -> bool:
    lowered = event_type.lower()
    return any(marker in lowered for marker in RULE_EVENT_MARKERS)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(limit, 1)]
