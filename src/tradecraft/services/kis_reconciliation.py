from __future__ import annotations

from typing import Any

from tradecraft.services.kis_ledger import (
    parse_iso_datetime as _parse_iso_datetime,
    positions_by_symbol as positions_by_symbol,
    safe_int as _safe_int,
    unallocated_qty_by_symbol as unallocated_qty_by_symbol,
)

__all__ = [
    "build_reconciliation_plan",
    "positions_by_symbol",
    "unallocated_qty_by_symbol",
]


def _block_time_key(block: dict[str, Any]) -> tuple[int, str, str]:
    for field in ("opened_at", "created_at", "updated_at"):
        parsed = _parse_iso_datetime(block.get(field))
        if parsed is not None:
            return (int(parsed.timestamp()), str(block.get("block_id") or ""), field)
    return (0, str(block.get("block_id") or ""), "")


def build_reconciliation_plan(
    *,
    account: dict[str, Any],
    blocks: list[dict[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    account_status = str(account.get("status") or "ok").strip().lower()
    if account_status not in {"", "ok"}:
        return {
            "status": "skipped",
            "reason": "account_snapshot_unavailable",
            "error_message": str(account.get("error_message") or ""),
            "symbols": {},
            "updates": [],
            "change_count": 0,
        }

    account_positions = {
        symbol: _safe_int(row.get("available_qty") or row.get("qty"))
        for symbol, row in positions_by_symbol(account).items()
    }
    by_symbol: dict[str, dict[str, int]] = {}
    updates: list[dict[str, Any]] = []
    blocks_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        symbol = str(block.get("symbol") or "")
        if not symbol:
            continue
        blocks_by_symbol.setdefault(symbol, []).append(block)

    for symbol in sorted(blocks_by_symbol):
        bucket = by_symbol.setdefault(
            symbol,
            {
                "account_qty": int(account_positions.get(symbol, 0)),
                "allocated_qty": 0,
                "overallocated_qty": 0,
            },
        )
        rows = blocks_by_symbol[symbol]
        existing_claims = sorted(
            [
                block
                for block in rows
                if str(block.get("status") or "") in {"open", "exit_pending"}
            ],
            key=_block_time_key,
        )
        entry_claims = sorted(
            [
                block
                for block in rows
                if str(block.get("status") or "") == "entry_pending"
            ],
            key=_block_time_key,
        )

        for block in existing_claims:
            status = str(block.get("status") or "")
            if status == "open":
                qty = max(_safe_int(block.get("qty_open")), 0)
                if qty <= 0:
                    continue
                available = max(bucket["account_qty"] - bucket["allocated_qty"], 0)
                if available >= qty:
                    bucket["allocated_qty"] += qty
                    continue
                bucket["overallocated_qty"] += qty - available
                if available > 0:
                    bucket["allocated_qty"] += available
                    updates.append(
                        {
                            "type": "open_partially_overallocated",
                            "block_id": str(block.get("block_id") or ""),
                            "fields": {
                                "qty_open": available,
                                "llm_reason": "open_block_partially_overallocated_reconciled",
                            },
                        }
                    )
                    continue
                updates.append(
                    {
                        "type": "open_overallocated",
                        "block_id": str(block.get("block_id") or ""),
                        "fields": {
                            "status": "error",
                            "qty_open": 0,
                            "force_exit_requested": 0,
                            "llm_reason": "open_block_overallocated_reconciled",
                        },
                    }
                )
                continue
            if status == "exit_pending":
                qty = max(_safe_int(block.get("qty_open")), 0)
                available = max(bucket["account_qty"] - bucket["allocated_qty"], 0)
                if available < qty:
                    updates.append(
                        {
                            "type": "exit_filled",
                            "block_id": str(block.get("block_id") or ""),
                            "fields": {
                                "status": "closed",
                                "qty_open": 0,
                                "closed_at": now_iso,
                                "force_exit_requested": 0,
                                "llm_reason": "exit_reconciled",
                            },
                        }
                    )
                else:
                    bucket["allocated_qty"] += qty

        for block in entry_claims:
            status = str(block.get("status") or "")
            if status == "entry_pending":
                available = max(bucket["account_qty"] - bucket["allocated_qty"], 0)
                qty = max(_safe_int(block.get("qty_initial")), 0)
                if available >= qty:
                    bucket["allocated_qty"] += qty
                    updates.append(
                        {
                            "type": "entry_filled",
                            "block_id": str(block.get("block_id") or ""),
                            "fields": {
                                "status": "open",
                                "qty_open": qty,
                                "opened_at": now_iso,
                                "llm_reason": "filled_reconciled",
                            },
                        }
                    )
                else:
                    bucket["allocated_qty"] += _safe_int(block.get("qty_open"))
    return {
        "status": "ok",
        "symbols": by_symbol,
        "updates": updates,
        "change_count": len(updates),
    }
