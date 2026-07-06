from __future__ import annotations

import json
from typing import Any

from tradecraft.services.kis_horizon import normalize_horizon

KIS_ETF_NAME_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "KBSTAR",
    "SOL",
    "RISE",
    "HANARO",
    "ARIRANG",
    "KOSEF",
    "TIMEFOLIO",
    "PLUS",
)
KIS_FEE_COST_KEYS = (
    "fee",
    "fees",
    "fee_krw",
    "fees_krw",
    "commission",
    "commission_krw",
    "ord_fee",
    "ord_fee_amt",
    "order_fee",
    "order_fee_amt",
    "tr_fee",
    "tr_fee_amt",
    "trade_fee",
    "trade_fee_amt",
    "fee_amt",
    "cmsn_amt",
    "ccld_fee",
    "ccld_fee_amt",
    "tot_fee",
    "tot_fee_amt",
    "total_fee",
    "total_fee_amt",
    "smtl_fee",
    "settlement_fee",
)
KIS_TAX_COST_KEYS = (
    "tax",
    "taxes",
    "tax_krw",
    "taxes_krw",
    "sell_tax",
    "sell_tax_krw",
    "tax_amt",
    "tr_tax",
    "tr_tax_amt",
    "trade_tax",
    "trade_tax_amt",
    "stock_tax",
    "stock_tax_amt",
    "ccld_tax",
    "ccld_tax_amt",
    "tot_tax",
    "tot_tax_amt",
    "total_tax",
    "total_tax_amt",
    "securities_transaction_tax",
)
KIS_FEE_TAX_COMBINED_KEYS = (
    "fee_tax",
    "fee_tax_krw",
    "fee_tax_amt",
    "total_fee_tax",
    "total_fee_tax_krw",
    "total_fee_tax_amt",
    "tax_fee",
    "tax_fee_krw",
    "tax_fee_amt",
    "tlex_amt",
    "total_tlex_amt",
    "settlement_fee_tax",
)
KIS_SLIPPAGE_COST_KEYS = (
    "slippage",
    "slippage_krw",
    "slippage_amt",
)
KIS_SPREAD_COST_KEYS = (
    "spread",
    "spread_krw",
    "spread_amt",
)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _safe_int(value: Any) -> int:
    return int(_safe_float(value))


def is_kis_etf_for_cost(*, symbol: str, name: str, horizon: str) -> bool:
    if horizon == "core_etf":
        return True
    _ = symbol
    name_text = str(name or "").strip().upper()
    return name_text.startswith(KIS_ETF_NAME_PREFIXES) or " ETF" in name_text


def kis_round_trip_cost_estimate(
    *,
    entry_price: float,
    exit_price: float,
    qty: int,
    is_etf: bool,
    buy_fee_rate: float,
    sell_fee_rate: float,
    sell_tax_rate: float,
    slippage_bps: float,
    spread_bps: float = 0.0,
) -> dict[str, Any]:
    buy_notional = abs(float(entry_price) * int(qty))
    sell_notional = abs(float(exit_price) * int(qty))
    round_trip_notional = buy_notional + sell_notional
    if buy_notional <= 0 or sell_notional <= 0 or int(qty) <= 0:
        return {
            "status": "missing",
            "source": "missing_price_or_qty",
            "fees": 0.0,
            "taxes": 0.0,
            "slippage": 0.0,
            "spread": 0.0,
            "funding": 0.0,
            "total": 0.0,
            "round_trip_notional_krw": round(round_trip_notional, 6),
        }
    fees = buy_notional * max(float(buy_fee_rate), 0.0)
    fees += sell_notional * max(float(sell_fee_rate), 0.0)
    taxes = 0.0 if is_etf else sell_notional * max(float(sell_tax_rate), 0.0)
    slippage = round_trip_notional * max(float(slippage_bps), 0.0) / 10_000.0
    spread = round_trip_notional * max(float(spread_bps), 0.0) / 10_000.0
    total = fees + taxes + slippage + spread
    return {
        "status": "estimated_from_notional",
        "source": "kis_validation_cost_model",
        "fees": round(fees, 6),
        "taxes": round(taxes, 6),
        "slippage": round(slippage, 6),
        "spread": round(spread, 6),
        "funding": 0.0,
        "total": round(total, 6),
        "round_trip_notional_krw": round(round_trip_notional, 6),
        "buy_notional_krw": round(buy_notional, 6),
        "sell_notional_krw": round(sell_notional, 6),
        "spread_bps": round(max(float(spread_bps), 0.0), 6),
        **({"tax_exempt_reason": "etf"} if is_etf else {}),
    }


def _json_loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _iter_kis_cost_payloads(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        parsed = _json_loads(value, {})
        if not parsed:
            return []
        return _iter_kis_cost_payloads(parsed)
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for item in value:
            rows.extend(_iter_kis_cost_payloads(item))
        return rows
    if not isinstance(value, dict):
        return []

    rows = [value]
    for key in ("raw", "response", "response_json", "output", "output1", "orders"):
        if key in value:
            rows.extend(_iter_kis_cost_payloads(value.get(key)))
    return rows


def _first_kis_cost_value(
    payloads: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> tuple[float, str]:
    normalized_keys = {key.lower() for key in keys}
    for row in payloads:
        for key, value in row.items():
            if str(key).lower() not in normalized_keys:
                continue
            cost = abs(_safe_float(value))
            if cost > 0:
                return cost, str(key)
    return 0.0, ""


def kis_close_cost_components(
    *,
    entry_price: float,
    exit_price: float,
    qty: int,
    is_etf: bool,
    buy_fee_rate: float,
    sell_fee_rate: float,
    sell_tax_rate: float,
    slippage_bps: float,
    spread_bps: float,
    payloads: list[Any],
) -> dict[str, Any]:
    estimated = kis_round_trip_cost_estimate(
        entry_price=entry_price,
        exit_price=exit_price,
        qty=qty,
        is_etf=is_etf,
        buy_fee_rate=buy_fee_rate,
        sell_fee_rate=sell_fee_rate,
        sell_tax_rate=sell_tax_rate,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
    )
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        rows.extend(_iter_kis_cost_payloads(payload))
    if not rows:
        return estimated

    fees, fee_source = _first_kis_cost_value(rows, KIS_FEE_COST_KEYS)
    taxes, tax_source = _first_kis_cost_value(rows, KIS_TAX_COST_KEYS)
    combined_fee_tax, combined_source = _first_kis_cost_value(
        rows,
        KIS_FEE_TAX_COMBINED_KEYS,
    )
    slippage, slippage_source = _first_kis_cost_value(rows, KIS_SLIPPAGE_COST_KEYS)
    spread, spread_source = _first_kis_cost_value(rows, KIS_SPREAD_COST_KEYS)

    explicit_components: dict[str, float] = {}
    component_sources: dict[str, str] = {}
    components = {
        "fees": _safe_float(estimated.get("fees")),
        "taxes": _safe_float(estimated.get("taxes")),
        "slippage": _safe_float(estimated.get("slippage")),
        "spread": _safe_float(estimated.get("spread")),
        "funding": 0.0,
    }

    split_cost_found = fees > 0 or taxes > 0
    if fees > 0:
        components["fees"] = fees
        explicit_components["fees"] = round(fees, 6)
        component_sources["fees"] = fee_source
    if taxes > 0:
        components["taxes"] = taxes
        explicit_components["taxes"] = round(taxes, 6)
        component_sources["taxes"] = tax_source
    if combined_fee_tax > 0 and not split_cost_found:
        components["fees"] = combined_fee_tax
        components["taxes"] = 0.0
        explicit_components["combined_fee_tax"] = round(combined_fee_tax, 6)
        component_sources["combined_fee_tax"] = combined_source
    if slippage > 0:
        components["slippage"] = slippage
        explicit_components["slippage"] = round(slippage, 6)
        component_sources["slippage"] = slippage_source
    if spread > 0:
        components["spread"] = spread
        explicit_components["spread"] = round(spread, 6)
        component_sources["spread"] = spread_source

    if not explicit_components:
        return estimated

    total = sum(components.values())
    market_costs_explicit = "slippage" in explicit_components or "spread" in explicit_components
    split_or_combined_costs_explicit = split_cost_found or combined_fee_tax > 0
    status = "explicit_order_costs"
    if split_or_combined_costs_explicit and not market_costs_explicit:
        status = "explicit_order_costs_plus_estimated_market_costs"
    if combined_fee_tax > 0 and not split_cost_found:
        status = "explicit_combined_fee_tax_plus_estimated_market_costs"

    return {
        **estimated,
        "status": status,
        "source": "kis_order_payload",
        "fees": round(components["fees"], 6),
        "taxes": round(components["taxes"], 6),
        "slippage": round(components["slippage"], 6),
        "spread": round(components["spread"], 6),
        "funding": 0.0,
        "total": round(total, 6),
        "explicit_components": explicit_components,
        "component_sources": component_sources,
        "estimated_components": {
            "fees": round(_safe_float(estimated.get("fees")), 6),
            "taxes": round(_safe_float(estimated.get("taxes")), 6),
            "slippage": round(_safe_float(estimated.get("slippage")), 6),
            "spread": round(_safe_float(estimated.get("spread")), 6),
            "funding": 0.0,
        },
    }


def kis_closed_block_performance_metadata(
    *,
    block: dict[str, Any],
    match: dict[str, Any],
    filled_qty: int,
    order: dict[str, Any],
    buy_fee_rate: float,
    sell_fee_rate: float,
    sell_tax_rate: float,
    slippage_bps: float,
    spread_bps: float,
    recorded_at: str,
) -> dict[str, Any]:
    qty = max(_safe_int(filled_qty), 0)
    entry_price = _safe_float(block.get("entry_price"))
    exit_price = (
        _safe_float(match.get("avg_fill_price"))
        or _safe_float(order.get("avg_fill_price"))
        or _safe_float(order.get("limit_price"))
    )
    if qty <= 0 or entry_price <= 0 or exit_price <= 0:
        return {}

    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    attribution = kis_block_performance_attribution(block)
    horizon = normalize_horizon(metadata.get("horizon"))
    is_etf = is_kis_etf_for_cost(
        symbol=str(block.get("symbol") or ""),
        name=str(block.get("name") or ""),
        horizon=horizon,
    )
    costs = kis_close_cost_components(
        entry_price=entry_price,
        exit_price=exit_price,
        qty=qty,
        is_etf=is_etf,
        buy_fee_rate=buy_fee_rate,
        sell_fee_rate=sell_fee_rate,
        sell_tax_rate=sell_tax_rate,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
        payloads=[match, order],
    )
    gross_pnl = (exit_price - entry_price) * qty
    total_cost = _safe_float(costs.get("total"))
    net_pnl = gross_pnl - total_cost
    performance = {
        "version": "kis_closed_block_performance_v1",
        "symbol": str(block.get("symbol") or ""),
        "name": str(block.get("name") or ""),
        "horizon": horizon,
        "entry_price": round(entry_price, 6),
        "exit_price": round(exit_price, 6),
        "qty": qty,
        "gross_pnl_krw": round(gross_pnl, 6),
        "net_pnl_krw": round(net_pnl, 6),
        "total_cost_krw": round(total_cost, 6),
        "cost_model_status": str(costs.get("status") or ""),
        "cost_source": str(costs.get("source") or ""),
        "cost_components": {
            "fees": round(_safe_float(costs.get("fees")), 6),
            "taxes": round(_safe_float(costs.get("taxes")), 6),
            "slippage": round(_safe_float(costs.get("slippage")), 6),
            "spread": round(_safe_float(costs.get("spread")), 6),
            "funding": 0.0,
        },
        "round_trip_notional_krw": costs.get("round_trip_notional_krw"),
        "buy_notional_krw": costs.get("buy_notional_krw"),
        "sell_notional_krw": costs.get("sell_notional_krw"),
        "is_etf": is_etf,
        "recorded_at": str(recorded_at or ""),
        "exit_reason": str(order.get("reason") or ""),
        **attribution,
    }
    for optional_key in (
        "explicit_components",
        "estimated_components",
        "component_sources",
    ):
        optional_value = costs.get(optional_key)
        if isinstance(optional_value, dict) and optional_value:
            performance[optional_key] = optional_value
    return performance


def kis_block_performance_attribution(block: dict[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    created_by = str(block.get("created_by") or "").strip().lower()
    if created_by == "existing_position" or bool(metadata.get("adopted_from_account")):
        return {
            "created_by": "existing_position" if created_by == "existing_position" else created_by,
            "entry_attribution": "external_existing_position",
            "management_attribution": "jue_block_management",
            "scorecard_attribution": "risk_management_only",
            "include_in_entry_alpha_scorecard": False,
            "entry_alpha_exclusion_reason": "existing_position_adoption",
        }
    return {
        "created_by": created_by or "llm",
        "entry_attribution": "jue_block_entry",
        "management_attribution": "jue_block_management",
        "scorecard_attribution": "entry_and_management",
        "include_in_entry_alpha_scorecard": True,
        "entry_alpha_exclusion_reason": "",
    }


def kis_cost_feasibility_payload(
    *,
    symbol: str,
    name: str,
    entry_price: float,
    target_price: float,
    stop_price: float,
    qty: int,
    horizon: str,
    buy_fee_rate: float,
    sell_fee_rate: float,
    sell_tax_rate: float,
    slippage_bps: float,
    spread_bps: float = 0.0,
) -> dict[str, Any]:
    qty = max(int(qty), 0)
    horizon = normalize_horizon(horizon)
    is_etf = is_kis_etf_for_cost(symbol=symbol, name=name, horizon=horizon)
    if entry_price <= 0 or target_price <= 0 or stop_price <= 0 or qty <= 0:
        return {
            "version": "kis_cost_feasibility_v1",
            "status": "missing",
            "symbol": str(symbol or ""),
            "reason": "missing_price_or_qty",
        }
    target_cost = kis_round_trip_cost_estimate(
        entry_price=entry_price,
        exit_price=target_price,
        qty=qty,
        is_etf=is_etf,
        buy_fee_rate=buy_fee_rate,
        sell_fee_rate=sell_fee_rate,
        sell_tax_rate=sell_tax_rate,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
    )
    stop_cost = kis_round_trip_cost_estimate(
        entry_price=entry_price,
        exit_price=stop_price,
        qty=qty,
        is_etf=is_etf,
        buy_fee_rate=buy_fee_rate,
        sell_fee_rate=sell_fee_rate,
        sell_tax_rate=sell_tax_rate,
        slippage_bps=slippage_bps,
        spread_bps=spread_bps,
    )
    gross_target_profit = max((target_price - entry_price) * qty, 0.0)
    gross_stop_loss = max((entry_price - stop_price) * qty, 0.0)
    target_cost_total = _safe_float(target_cost.get("total"))
    stop_cost_total = _safe_float(stop_cost.get("total"))
    net_target_profit = gross_target_profit - target_cost_total
    net_stop_loss = gross_stop_loss + stop_cost_total
    target_cost_multiple = (
        gross_target_profit / target_cost_total
        if target_cost_total > 0
        else 999.0
    )
    gross_reward_risk = (
        gross_target_profit / gross_stop_loss
        if gross_stop_loss > 0
        else 0.0
    )
    net_reward_risk = (
        net_target_profit / net_stop_loss
        if net_target_profit > 0 and net_stop_loss > 0
        else 0.0
    )
    if net_target_profit <= 0 or target_cost_multiple < 1.25:
        status = "fail"
        design_note = "target move is too narrow after estimated round-trip costs"
    elif target_cost_multiple < 3.0 or net_reward_risk < 0.5:
        status = "warn"
        design_note = "cost-adjusted edge is thin; demand better entry or wider target"
    else:
        status = "pass"
        design_note = "target/stop structure leaves positive room after estimated costs"
    return {
        "version": "kis_cost_feasibility_v1",
        "status": status,
        "symbol": str(symbol or ""),
        "horizon": horizon,
        "qty": qty,
        "entry_price": round(entry_price, 6),
        "target_price": round(target_price, 6),
        "stop_price": round(stop_price, 6),
        "gross_target_profit_krw": round(gross_target_profit, 6),
        "gross_stop_loss_krw": round(gross_stop_loss, 6),
        "target_round_trip_cost_krw": round(target_cost_total, 6),
        "stop_round_trip_cost_krw": round(stop_cost_total, 6),
        "net_target_profit_after_cost_krw": round(net_target_profit, 6),
        "net_stop_loss_after_cost_krw": round(net_stop_loss, 6),
        "target_cost_multiple": round(target_cost_multiple, 6),
        "gross_reward_risk": round(gross_reward_risk, 6),
        "net_reward_risk": round(net_reward_risk, 6),
        "is_etf": is_etf,
        "cost_assumptions": {
            "buy_fee_rate": round(max(float(buy_fee_rate), 0.0), 8),
            "sell_fee_rate": round(max(float(sell_fee_rate), 0.0), 8),
            "sell_tax_rate": round(0.0 if is_etf else max(float(sell_tax_rate), 0.0), 8),
            "slippage_bps": round(max(float(slippage_bps), 0.0), 6),
            "spread_bps": round(max(float(spread_bps), 0.0), 6),
        },
        "target_cost_components": target_cost,
        "stop_cost_components": stop_cost,
        "design_note": design_note,
    }
