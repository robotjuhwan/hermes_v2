from __future__ import annotations

from tradecraft.services.kis_manager_candidates import (
    manager_symbols,
    symbols_for_quotes,
)


def test_symbols_for_quotes_keeps_account_then_blocks_unique_and_limited() -> None:
    symbols = symbols_for_quotes(
        blocks=[
            {"symbol": "000660"},
            {"symbol": "123"},
            {"symbol": "005930"},
            {"symbol": "035420"},
        ],
        account={
            "positions": [
                {"symbol": "005930"},
                {"symbol": "not-symbol"},
                {"symbol": "000660"},
            ]
        },
        limit=3,
    )

    assert symbols == ["005930", "000660", "035420"]


def test_manager_symbols_preserves_owned_symbols_and_balances_strategy_candidates() -> None:
    selected = manager_symbols(
        account={"positions": [{"symbol": "005930"}]},
        blocks=[{"symbol": "000660"}],
        strategy_payload={
            "candidates": [
                {"symbol": "069500", "asset_class": "etf", "score": 100},
                {"symbol": "091160", "asset_class": "etf", "score": 99},
                {"symbol": "402340", "asset_class": "equity", "score": 80},
                {"symbol": "178920", "asset_class": "equity", "score": 79},
            ]
        },
        limit=5,
    )

    assert selected[:2] == ["005930", "000660"]
    assert "069500" in selected
    assert "402340" in selected
    assert len(selected) == 5
