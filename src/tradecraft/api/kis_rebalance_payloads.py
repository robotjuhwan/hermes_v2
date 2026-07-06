from __future__ import annotations

from typing import Any


def _safe_positive_float(value: Any) -> float:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def build_kis_block_rebalance_status_payload(
    block_snapshot: dict[str, Any],
    *,
    updated_at: str,
    primary_ready: bool,
) -> dict[str, Any]:
    snapshot = block_snapshot if isinstance(block_snapshot, dict) else {}
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    horizon = (
        snapshot.get("horizon_allocation")
        if isinstance(snapshot.get("horizon_allocation"), dict)
        else {}
    )
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    config = summary.get("config") if isinstance(summary.get("config"), dict) else {}
    items = [row for row in list(horizon.get("items") or []) if isinstance(row, dict)]
    targets = horizon.get("targets") if isinstance(horizon.get("targets"), dict) else {}
    total_value = _safe_positive_float(
        horizon.get("total_value_krw")
        or account.get("total_value_krw")
        or account.get("total_equity_krw")
    )
    cash_value = _safe_positive_float(account.get("cash_krw"))
    position_value = _safe_positive_float(account.get("position_value_krw"))
    if total_value <= 0:
        total_value = cash_value + position_value

    labels = {
        "short": "단기 블록",
        "mid": "중기 블록",
        "long": "장기 블록",
        "core_etf": "ETF/Core",
        "cash": "현금",
    }

    def row_key(row: dict[str, Any]) -> str:
        raw = str(row.get("horizon") or row.get("block_color") or "").strip()
        return raw or "unknown"

    def row_ticker(key: str) -> str:
        return "KRW" if key == "cash" else key

    ordered_items = sorted(
        items,
        key=lambda row: (
            0
            if row_key(row) == "cash"
            else 1
            if row_key(row) == "short"
            else 2
            if row_key(row) == "mid"
            else 3
            if row_key(row) == "long"
            else 4
            if row_key(row) == "core_etf"
            else 9,
            row_key(row),
        ),
    )
    target_rows = [
        {
            "ticker": row_ticker(key),
            "name": labels.get(key, key),
            "weight": max(0.0, min(1.0, _safe_positive_float(row.get("target_weight")))),
        }
        for row in ordered_items
        for key in [row_key(row)]
    ]
    current_rows = [
        {
            "ticker": row_ticker(key),
            "name": labels.get(key, key),
            "weight": max(0.0, min(1.0, _safe_positive_float(row.get("current_weight")))),
        }
        for row in ordered_items
        for key in [row_key(row)]
    ]

    target_cash_weight = max(
        0.0,
        min(
            1.0,
            _safe_positive_float(targets.get("cash"))
            if targets
            else next(
                (
                    _safe_positive_float(row.get("target_weight"))
                    for row in ordered_items
                    if row_key(row) == "cash"
                ),
                0.0,
            ),
        ),
    )
    target_invested_ratio = max(0.0, min(1.0, 1.0 - target_cash_weight))
    actual_invested_ratio = (
        max(0.0, min(1.0, position_value / total_value))
        if total_value > 0
        else sum(
            _safe_positive_float(row.get("current_weight"))
            for row in ordered_items
            if row_key(row) != "cash"
        )
    )

    active_blocks = [
        row for row in list(snapshot.get("active_blocks") or []) if isinstance(row, dict)
    ]
    open_pairs = []
    for row in active_blocks:
        symbol = str(row.get("symbol") or "").strip()
        name = str(row.get("name") or symbol).strip()
        if symbol:
            open_pairs.append(f"{symbol}/{name or symbol}")

    return {
        "status": "ok",
        "updated_at": updated_at,
        "source": "kis_block_trader",
        "target": {
            "updated_at": str(snapshot.get("updated_at") or updated_at),
            "target_cash_weight": target_cash_weight,
            "target_invested_ratio": target_invested_ratio,
            "rows": target_rows,
        },
        "current": {
            "updated_at": str(account.get("captured_at") or snapshot.get("updated_at") or updated_at),
            "rows": current_rows,
        },
        "execution": {
            "open_trade_count": int(summary.get("open_block_count") or len(active_blocks)),
            "open_pairs": open_pairs[:20],
            "open_stake_total_krw": position_value,
            "total_value_krw": total_value,
            "actual_invested_ratio": actual_invested_ratio,
            "recent_order_count": int(summary.get("order_count") or snapshot.get("order_count") or 0),
            "errors": [],
        },
        "strategy_config": {
            "source": "kis_block_trader",
            "bot_id": "kis_block_trader",
            "api_connected": bool(primary_ready),
            "show_config": {
                "bot_name": "KIS Block Trader",
                "state": "block_trading",
                "runmode": str(summary.get("execution_mode") or snapshot.get("execution_mode") or "unknown"),
                "strategy": "JueBlockManager+RuleExecutor",
                "strategy_version": "block-v1",
                "timeframe": "KRX session / block horizon",
                "trading_mode": "kis_blocks",
                "max_open_trades": int(summary.get("open_block_count") or 0),
                "stake_currency": "KRW",
                "stake_amount": str(account.get("orderable_cash_krw") or account.get("cash_krw") or 0),
                "available_capital": _safe_positive_float(
                    account.get("orderable_cash_krw") or account.get("cash_krw")
                ),
                "force_entry_enable": False,
                "position_adjustment_enable": True,
                "max_entry_position_adjustment": "",
            },
            "override": {
                "target_weights_updated_at": str(snapshot.get("updated_at") or updated_at),
                "target_cash_weight": target_cash_weight,
                "portfolio_total_krw": total_value,
                "force_entry_enable": False,
                "pair_whitelist_count": len(
                    [row for row in target_rows if str(row.get("ticker")) != "KRW"]
                ),
                "manager_interval_sec": config.get("manager_interval_sec"),
                "rule_interval_sec": config.get("rule_interval_sec"),
            },
            "errors": [],
        },
    }
