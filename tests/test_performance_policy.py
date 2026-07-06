from __future__ import annotations

import pytest

from tradecraft.services.performance_policy import (
    performance_max_drawdown,
    performance_profit_factor,
    performance_recovery_factor,
)


def test_performance_profit_factor_handles_wins_losses_and_no_loss_cases() -> None:
    assert performance_profit_factor([10.0, -5.0, 2.5, -2.5]) == pytest.approx(1.6666667)
    assert performance_profit_factor([10.0, 2.0]) == pytest.approx(999.0)
    assert performance_profit_factor([0.0, 0.0]) == pytest.approx(0.0)


def test_performance_max_drawdown_uses_cumulative_path() -> None:
    assert performance_max_drawdown([5.0, -2.0, -4.0, 3.0, -1.0]) == pytest.approx(-6.0)
    assert performance_max_drawdown([1.0, 2.0, 3.0]) == pytest.approx(0.0)


def test_performance_recovery_factor_handles_no_drawdown_cases() -> None:
    assert performance_recovery_factor(total_return=12.0, max_drawdown=-4.0) == pytest.approx(3.0)
    assert performance_recovery_factor(total_return=12.0, max_drawdown=0.0) == pytest.approx(999.0)
    assert performance_recovery_factor(total_return=0.0, max_drawdown=0.0) == pytest.approx(0.0)
