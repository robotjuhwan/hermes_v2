from __future__ import annotations

import sqlite3
from typing import Any

from tradecraft.services.binance_lane import canonical_binance_performance_lane
from tradecraft.services.binance_ledger import ledger_json_loads, safe_float
from tradecraft.services.binance_symbol import (
    explicit_market_scope,
    normalize_market,
    normalize_position_side,
)


def partition_performance_rows(
    rows: list[sqlite3.Row],
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    clean_rows: list[sqlite3.Row] = []
    excluded_rows: list[sqlite3.Row] = []
    for row in rows:
        if (
            row_is_malformed_market_scope_execution(row)
            or row_has_invalid_price_geometry(row)
            or row_is_reconciliation_only_close(row)
        ):
            excluded_rows.append(row)
            continue
        clean_rows.append(row)
    return clean_rows, excluded_rows


def execution_defect_risk_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    reason_counts: dict[str, int] = {}
    scope_counts: dict[str, int] = {}
    scope_loss_usdt: dict[str, float] = {}
    examples: list[dict[str, Any]] = []
    excluded_pnl = 0.0
    excluded_loss = 0.0
    excluded_gain = 0.0
    for row in rows:
        keys = set(row.keys())
        pnl = safe_float(
            row["net_pnl_usdt"] if "net_pnl_usdt" in keys else row["pnl_usdt"]
        )
        excluded_pnl += pnl
        if pnl < 0:
            excluded_loss += abs(pnl)
        elif pnl > 0:
            excluded_gain += pnl
        reasons = execution_defect_reasons_for_row(row)
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        market = normalize_market(row["market"] if "market" in keys else "")
        side = normalize_position_side(row["side"] if "side" in keys else "")
        lane = canonical_binance_performance_lane(
            raw_lane=row["lane"] if "lane" in keys else "",
            market=market,
            side=side,
        )
        for scope in {f"{market}:{side}", lane}:
            if not scope:
                continue
            scope_counts[scope] = scope_counts.get(scope, 0) + 1
            if pnl < 0:
                scope_loss_usdt[scope] = scope_loss_usdt.get(scope, 0.0) + abs(pnl)
        if len(examples) < 5:
            examples.append(
                {
                    "block_id": str(row["block_id"] if "block_id" in keys else ""),
                    "symbol": str(row["symbol"] if "symbol" in keys else ""),
                    "market": normalize_market(row["market"] if "market" in keys else ""),
                    "side": normalize_position_side(row["side"] if "side" in keys else ""),
                    "pnl_usdt": pnl,
                    "reasons": reasons,
                }
            )
    status = "clear"
    instruction = "No execution-defect pressure in this performance window."
    if rows and excluded_loss > 0:
        status = "elevated"
        instruction = (
            "Do not scale: execution defects lost money. Use smaller waiting "
            "blocks and verify market/scope/geometry before pressing size."
        )
    elif rows:
        status = "watch"
        instruction = (
            "Excluded execution defects exist; keep them out of edge scorecards "
            "but audit them before increasing size."
        )
    return {
        "version": "binance_execution_defect_risk_v1",
        "status": status,
        "excluded_count": len(rows),
        "excluded_pnl_usdt": excluded_pnl,
        "excluded_loss_usdt": excluded_loss,
        "excluded_gain_usdt": excluded_gain,
        "reasons": reason_counts,
        "scope_counts": scope_counts,
        "scope_loss_usdt": {
            scope: round(loss, 8) for scope, loss in scope_loss_usdt.items()
        },
        "examples": examples,
        "instruction": instruction,
    }


def execution_defect_reasons_for_row(row: sqlite3.Row) -> list[str]:
    reasons: list[str] = []
    if row_is_malformed_market_scope_execution(row):
        reasons.append("malformed_market_scope")
    if row_has_invalid_price_geometry(row):
        reasons.append("invalid_price_geometry")
    if row_is_reconciliation_only_close(row):
        reasons.append("reconciliation_only_close")
    return reasons or ["unknown_execution_defect"]


def row_has_invalid_price_geometry(row: sqlite3.Row) -> bool:
    keys = set(row.keys())
    entry = safe_float(row["entry_price"] if "entry_price" in keys else 0)
    target = safe_float(row["target_price"] if "target_price" in keys else 0)
    stop = safe_float(row["stop_price"] if "stop_price" in keys else 0)
    lesson = ledger_json_loads(row["lesson_json"], {}) if "lesson_json" in keys else {}
    if isinstance(lesson, dict):
        risk_stop = safe_float(lesson.get("risk_stop_price"))
        if risk_stop > 0:
            stop = risk_stop
    if entry <= 0 or target <= 0 or stop <= 0:
        return False
    side = normalize_position_side(row["side"] if "side" in keys else "")
    if side == "short":
        return not (target < entry < stop)
    return not (stop < entry < target)


def row_is_reconciliation_only_close(row: sqlite3.Row) -> bool:
    keys = set(row.keys())
    metadata = (
        ledger_json_loads(row["block_metadata_json"], {})
        if "block_metadata_json" in keys
        else {}
    )
    if not isinstance(metadata, dict):
        return False
    reconciliation = metadata.get("exit_reconciled_missing_asset")
    return isinstance(reconciliation, dict) and bool(reconciliation)


def row_is_malformed_market_scope_execution(row: sqlite3.Row) -> bool:
    keys = set(row.keys())
    block_market = normalize_market(
        row["block_market"] if "block_market" in keys else row["market"]
    )
    if block_market == "futures":
        return False
    metadata = (
        ledger_json_loads(row["block_metadata_json"], {})
        if "block_metadata_json" in keys
        else {}
    )
    if not isinstance(metadata, dict):
        return False
    horizon = str(metadata.get("horizon") or "").strip().lower()
    block_color = str(metadata.get("block_color") or "").strip().lower()
    scope = explicit_market_scope(metadata.get("market_or_account_scope"))
    raw_horizon = str(metadata.get("manager_contract_raw_horizon") or "").strip().lower()
    if (
        horizon == "futures"
        or block_color == "futures"
        or raw_horizon == "futures"
        or scope == "futures"
    ):
        return True
    text = " ".join(
        str(row[key] or "")
        for key in ("block_thesis", "block_llm_reason")
        if key in keys
    ).lower()
    if not text:
        return False
    futures_lane_terms = (
        "futures long",
        "futures short",
        "future long",
        "future short",
        "선물 롱",
        "선물 숏",
        "선물 long",
        "선물 short",
    )
    if any(term in text for term in futures_lane_terms):
        return True
    futures_terms = ("futures", "future", "선물")
    isolation_terms = ("1x", "1배", "isolated", "격리")
    return any(term in text for term in futures_terms) and any(
        term in text for term in isolation_terms
    )
