from __future__ import annotations

from typing import Any, Callable

from tradecraft.services.binance_ledger import safe_float
from tradecraft.services.performance_policy import (
    performance_max_drawdown,
    performance_profit_factor,
    performance_recovery_factor,
)


def empty_performance_scorecard() -> dict[str, Any]:
    return {
        "sample_count": 0,
        "avg_r_multiple": 0.0,
        "avg_mfe_r_multiple": 0.0,
        "avg_mae_r_multiple": 0.0,
        "win_rate_pct": 0.0,
        "realized_pnl_usdt": 0.0,
        "gross_realized_pnl_usdt": 0.0,
        "total_cost_usdt": 0.0,
        "avg_pnl_usdt": 0.0,
        "gross_profit_usdt": 0.0,
        "gross_loss_usdt": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_usdt": 0.0,
        "max_drawdown_r_multiple": 0.0,
        "recovery_factor": 0.0,
        "symbol_scorecards": [],
        "side_scorecards": [],
        "lane_scorecards": [],
        "entry_quality_scorecards": [],
        "improvement_points": [],
        "pattern_scorecards": [],
        "recent_lessons": [],
    }


def performance_scorecard_from_reflections(
    reflections: list[dict[str, Any]],
) -> dict[str, Any]:
    sample_count = len(reflections)
    if sample_count <= 0:
        return empty_performance_scorecard()
    r_values = [safe_float(row.get("r_multiple")) for row in reflections]
    mfe_values = [safe_float(row.get("mfe_r_multiple")) for row in reflections]
    mae_values = [safe_float(row.get("mae_r_multiple")) for row in reflections]
    pnl_values = [safe_float(row.get("pnl_usdt")) for row in reflections]
    gross_pnl_values = [
        safe_float(row.get("gross_pnl_usdt") or row.get("pnl_usdt"))
        for row in reflections
    ]
    total_cost = sum(
        safe_float(row.get("fee_usdt"))
        + safe_float(row.get("funding_usdt"))
        + safe_float(row.get("slippage_usdt"))
        + safe_float(row.get("spread_usdt"))
        for row in reflections
    )
    win_count = len([value for value in r_values if value > 0])
    realized_pnl = sum(pnl_values)
    max_drawdown_usdt = performance_max_drawdown(pnl_values)
    max_drawdown_r = performance_max_drawdown(r_values)
    return {
        "sample_count": sample_count,
        "avg_r_multiple": sum(r_values) / sample_count,
        "avg_mfe_r_multiple": sum(mfe_values) / sample_count,
        "avg_mae_r_multiple": sum(mae_values) / sample_count,
        "win_rate_pct": win_count / sample_count * 100.0,
        "realized_pnl_usdt": realized_pnl,
        "gross_realized_pnl_usdt": sum(gross_pnl_values),
        "total_cost_usdt": total_cost,
        "avg_pnl_usdt": sum(pnl_values) / sample_count,
        "gross_profit_usdt": sum(value for value in pnl_values if value > 0),
        "gross_loss_usdt": sum(value for value in pnl_values if value < 0),
        "profit_factor": performance_profit_factor(pnl_values),
        "max_drawdown_usdt": max_drawdown_usdt,
        "max_drawdown_r_multiple": max_drawdown_r,
        "recovery_factor": performance_recovery_factor(
            total_return=realized_pnl,
            max_drawdown=max_drawdown_usdt,
        ),
        "symbol_scorecards": performance_group_scorecards(
            reflections,
            key_name="symbol",
            key_fn=lambda row: str(row.get("symbol") or "UNKNOWN"),
        ),
        "side_scorecards": performance_group_scorecards(
            reflections,
            key_name="side",
            key_fn=lambda row: (
                f"{row.get('market') or 'spot'}:{row.get('side') or 'long'}"
            ),
        ),
        "lane_scorecards": performance_group_scorecards(
            reflections,
            key_name="lane",
            key_fn=lambda row: (
                str(row.get("lane") or "").strip()
                or f"{row.get('market') or 'spot'}:{row.get('side') or 'long'}"
            ),
        ),
        "entry_quality_scorecards": performance_entry_quality_scorecards(reflections),
        "improvement_points": performance_improvement_points(reflections),
        "pattern_scorecards": performance_pattern_scorecards(reflections),
        "recent_lessons": [
            row.get("lesson") or {}
            for row in reflections
            if isinstance(row.get("lesson"), dict)
        ][:5],
    }


def performance_group_scorecards(
    reflections: list[dict[str, Any]],
    *,
    key_name: str,
    key_fn: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in reflections:
        key = key_fn(row).strip() or "UNKNOWN"
        grouped.setdefault(key, []).append(row)
    cards: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        r_values = [safe_float(row.get("r_multiple")) for row in rows]
        mfe_values = [safe_float(row.get("mfe_r_multiple")) for row in rows]
        mae_values = [safe_float(row.get("mae_r_multiple")) for row in rows]
        pnl_values = [safe_float(row.get("pnl_usdt")) for row in rows]
        sample_count = len(rows)
        win_count = len([value for value in r_values if value > 0])
        realized_pnl = sum(pnl_values)
        max_drawdown_usdt = performance_max_drawdown(pnl_values)
        max_drawdown_r = performance_max_drawdown(r_values)
        cards.append(
            {
                key_name: key,
                "sample_count": sample_count,
                "pnl_usdt": realized_pnl,
                "gross_profit_usdt": sum(value for value in pnl_values if value > 0),
                "gross_loss_usdt": sum(value for value in pnl_values if value < 0),
                "profit_factor": performance_profit_factor(pnl_values),
                "max_drawdown_usdt": max_drawdown_usdt,
                "max_drawdown_r_multiple": max_drawdown_r,
                "recovery_factor": performance_recovery_factor(
                    total_return=realized_pnl,
                    max_drawdown=max_drawdown_usdt,
                ),
                "avg_r_multiple": sum(r_values) / sample_count if sample_count else 0.0,
                "avg_mfe_r_multiple": (
                    sum(mfe_values) / sample_count if sample_count else 0.0
                ),
                "avg_mae_r_multiple": (
                    sum(mae_values) / sample_count if sample_count else 0.0
                ),
                "win_rate_pct": (
                    win_count / sample_count * 100.0 if sample_count else 0.0
                ),
            }
        )
    cards.sort(
        key=lambda row: (
            safe_float(row.get("pnl_usdt")),
            -int(row.get("sample_count") or 0),
        )
    )
    return cards[:8]


def performance_entry_quality_scorecards(
    reflections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scoped = [
        row
        for row in reflections
        if str(row.get("entry_quality_label") or "").strip()
    ]
    if not scoped:
        return []
    return performance_group_scorecards(
        scoped,
        key_name="entry_quality_lane",
        key_fn=lambda row: (
            f"{row.get('market') or 'spot'}:"
            f"{row.get('side') or 'long'}:"
            f"{row.get('entry_quality_label') or 'unknown'}"
        ),
    )


def performance_improvement_points(reflections: list[dict[str, Any]]) -> list[str]:
    points: list[str] = []
    total_pnl = sum(safe_float(row.get("pnl_usdt")) for row in reflections)
    avg_r = (
        sum(safe_float(row.get("r_multiple")) for row in reflections) / len(reflections)
        if reflections
        else 0.0
    )
    if total_pnl < 0:
        points.append(
            f"Recent realized PnL is negative ({total_pnl:.4f} USDT, avg R {avg_r:.2f}); reduce churn and require stronger confirmation."
        )

    symbol_cards = performance_group_scorecards(
        reflections,
        key_name="symbol",
        key_fn=lambda row: str(row.get("symbol") or "UNKNOWN"),
    )
    for card in symbol_cards:
        if int(card.get("sample_count") or 0) >= 2 and safe_float(card.get("pnl_usdt")) < 0:
            points.append(
                f"{card['symbol']} repeated losses: {card['sample_count']} samples, {card['pnl_usdt']:.4f} USDT, win {card['win_rate_pct']:.1f}%; consider cooldown or smaller size."
            )
            break

    side_cards = performance_group_scorecards(
        reflections,
        key_name="side",
        key_fn=lambda row: f"{row.get('market') or 'spot'}:{row.get('side') or 'long'}",
    )
    for card in side_cards:
        if int(card.get("sample_count") or 0) >= 3 and safe_float(card.get("pnl_usdt")) < 0:
            points.append(
                f"{card['side']} is underperforming: {card['sample_count']} samples, {card['pnl_usdt']:.4f} USDT, win {card['win_rate_pct']:.1f}%; demand higher edge before adding similar blocks."
            )
            break

    lane_cards = performance_group_scorecards(
        reflections,
        key_name="lane",
        key_fn=lambda row: (
            str(row.get("lane") or "").strip()
            or f"{row.get('market') or 'spot'}:{row.get('side') or 'long'}"
        ),
    )
    for card in lane_cards:
        if int(card.get("sample_count") or 0) >= 3 and safe_float(card.get("pnl_usdt")) < 0:
            points.append(
                f"{card['lane']} lane is underperforming: {card['sample_count']} samples, {card['pnl_usdt']:.4f} USDT, win {card['win_rate_pct']:.1f}%; shrink sizing until live edge improves."
            )
            break

    mfe_values = [safe_float(row.get("mfe_r_multiple")) for row in reflections]
    if reflections and sum(mfe_values) / len(reflections) > 0.7 and avg_r < 0:
        points.append(
            "MFE is available but final R is negative; review target/stop timing and profit protection."
        )
    return points[:5]


def performance_pattern_scorecards(
    reflections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in reflections:
        key = str(
            row.get("pattern_key")
            or f"{row.get('market', 'spot')}:{row.get('side', 'long')}"
        )
        grouped.setdefault(key, []).append(row)
    cards: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        r_values = [safe_float(row.get("r_multiple")) for row in rows]
        pnl_values = [safe_float(row.get("pnl_usdt")) for row in rows]
        sample_count = len(r_values)
        if sample_count <= 0:
            continue
        realized_pnl = sum(pnl_values)
        max_drawdown_usdt = performance_max_drawdown(pnl_values)
        max_drawdown_r = performance_max_drawdown(r_values)
        cards.append(
            {
                "pattern_key": key,
                "sample_count": sample_count,
                "pnl_usdt": realized_pnl,
                "gross_profit_usdt": sum(value for value in pnl_values if value > 0),
                "gross_loss_usdt": sum(value for value in pnl_values if value < 0),
                "profit_factor": performance_profit_factor(pnl_values),
                "max_drawdown_usdt": max_drawdown_usdt,
                "max_drawdown_r_multiple": max_drawdown_r,
                "recovery_factor": performance_recovery_factor(
                    total_return=realized_pnl,
                    max_drawdown=max_drawdown_usdt,
                ),
                "avg_r_multiple": sum(r_values) / sample_count,
                "win_rate_pct": (
                    len([value for value in r_values if value > 0])
                    / sample_count
                    * 100.0
                ),
            }
        )
    return sorted(
        cards,
        key=lambda row: (
            int(row.get("sample_count") or 0),
            safe_float(row.get("pnl_usdt")),
        ),
        reverse=True,
    )[:8]
