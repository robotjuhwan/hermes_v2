from __future__ import annotations

from pathlib import Path

from tradecraft.services.kis_price import (
    aggressive_limit_price,
    krx_tick_size,
    round_policy_krx_price,
)


def test_kis_block_trader_does_not_reown_price_helpers() -> None:
    source = Path("src/tradecraft/services/kis_block_trader.py").read_text()

    assert "def krx_tick_size(" not in source
    assert "def aggressive_limit_price(" not in source
    assert "def _round_policy_krx_price(" not in source


def test_krx_tick_size_matches_domestic_price_bands() -> None:
    assert krx_tick_size(999) == 1
    assert krx_tick_size(1_000) == 5
    assert krx_tick_size(4_999) == 5
    assert krx_tick_size(5_000) == 10
    assert krx_tick_size(10_000) == 50
    assert krx_tick_size(50_000) == 100
    assert krx_tick_size(100_000) == 500
    assert krx_tick_size(500_000) == 1_000


def test_aggressive_limit_price_moves_buy_up_and_sell_down_to_tick() -> None:
    assert aggressive_limit_price(100_000, side="buy", bps=30) == 100_500
    assert aggressive_limit_price(100_000, side="sell", bps=30) == 99_700
    assert aggressive_limit_price(0, side="buy") == 0


def test_round_policy_krx_price_rounds_targets_up_and_stops_down() -> None:
    assert round_policy_krx_price(100_240, field="target_price") == 100_500
    assert round_policy_krx_price(100_240, field="stop_price") == 100_000
    assert round_policy_krx_price(-1, field="target_price") == 0
