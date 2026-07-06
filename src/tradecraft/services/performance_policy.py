from __future__ import annotations


def performance_profit_factor(values: list[float]) -> float:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return 999.0 if gross_profit > 0 else 0.0


def performance_max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return max_drawdown


def performance_recovery_factor(*, total_return: float, max_drawdown: float) -> float:
    if max_drawdown < 0:
        return total_return / abs(max_drawdown)
    return 999.0 if total_return > 0 else 0.0
