from __future__ import annotations

from tradecraft.services.kis_config_policy import (
    DEFAULT_ETF_UNIVERSE,
    DEFAULT_HORIZON_TARGETS,
    parse_etf_universe,
    parse_horizon_targets,
)


def test_parse_horizon_targets_keeps_defaults_and_normalizes_aliases() -> None:
    targets = parse_horizon_targets("단기:0.1, mid-term:0.35, long:0.2, cash:0.25")

    assert targets["short"] == 0.1
    assert targets["mid"] == 0.35
    assert targets["long"] == 0.2
    assert targets["cash"] == 0.25
    assert targets["core_etf"] == DEFAULT_HORIZON_TARGETS["core_etf"]


def test_parse_horizon_targets_ignores_invalid_or_non_positive_weights() -> None:
    targets = parse_horizon_targets({"short": "-1", "etf": "0.18", "unknown": "bad"})

    assert targets["short"] == DEFAULT_HORIZON_TARGETS["short"]
    assert targets["core_etf"] == 0.18


def test_parse_etf_universe_accepts_dicts_and_symbol_name_pairs() -> None:
    rows = parse_etf_universe(
        [
            {"symbol": "069500", "name": "KODEX 200"},
            {"symbol": "bad", "name": "DROP"},
            "091160:KODEX 반도체",
            "102110",
        ]
    )

    assert rows == [
        {"symbol": "069500", "name": "KODEX 200"},
        {"symbol": "091160", "name": "KODEX 반도체"},
    ]


def test_parse_etf_universe_falls_back_to_default_copy() -> None:
    rows = parse_etf_universe("bad,12345:invalid")

    assert rows == DEFAULT_ETF_UNIVERSE
    assert rows is not DEFAULT_ETF_UNIVERSE
