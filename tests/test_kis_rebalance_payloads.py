from __future__ import annotations

from pathlib import Path

from tradecraft.api.kis_rebalance_payloads import (
    build_kis_block_rebalance_status_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def test_kis_rebalance_payload_lives_outside_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    payloads_source = (
        ROOT / "src/tradecraft/api/kis_rebalance_payloads.py"
    ).read_text()

    assert "def build_kis_block_rebalance_status_payload(" in payloads_source
    assert "build_retired_kis_trader_readiness" not in payloads_source
    assert "def _build_kis_trader_readiness(" not in main_source


def test_build_kis_block_rebalance_status_payload_uses_horizon_allocation() -> None:
    payload = build_kis_block_rebalance_status_payload(
        {
            "updated_at": "2026-06-30T01:00:00+00:00",
            "account": {
                "captured_at": "2026-06-30T01:01:00+00:00",
                "cash_krw": 700_000,
                "orderable_cash_krw": 650_000,
                "position_value_krw": 300_000,
                "total_value_krw": 1_000_000,
            },
            "summary": {
                "open_block_count": 2,
                "order_count": 5,
                "execution_mode": "live",
                "config": {
                    "manager_interval_sec": 1800,
                    "rule_interval_sec": 10,
                },
            },
            "active_blocks": [
                {"symbol": "005930", "name": "삼성전자"},
                {"symbol": "360750", "name": "TIGER미국S&P500"},
            ],
            "horizon_allocation": {
                "targets": {
                    "cash": 0.3,
                    "short": 0.15,
                    "mid": 0.3,
                    "long": 0.15,
                    "core_etf": 0.1,
                },
                "total_value_krw": 1_000_000,
                "items": [
                    {
                        "horizon": "core_etf",
                        "current_weight": 0.1,
                        "target_weight": 0.1,
                    },
                    {
                        "horizon": "cash",
                        "current_weight": 0.7,
                        "target_weight": 0.3,
                    },
                    {
                        "horizon": "mid",
                        "current_weight": 0.2,
                        "target_weight": 0.3,
                    },
                ],
            },
        },
        updated_at="2026-06-30T01:02:00+00:00",
        primary_ready=True,
    )

    assert payload["source"] == "kis_block_trader"
    assert payload["target"]["target_cash_weight"] == 0.3
    assert payload["target"]["target_invested_ratio"] == 0.7
    assert payload["execution"]["open_trade_count"] == 2
    assert payload["execution"]["open_pairs"] == [
        "005930/삼성전자",
        "360750/TIGER미국S&P500",
    ]
    assert payload["execution"]["actual_invested_ratio"] == 0.3
    assert payload["strategy_config"]["source"] == "kis_block_trader"
    assert payload["strategy_config"]["show_config"]["bot_name"] == "KIS Block Trader"
    assert payload["strategy_config"]["override"]["manager_interval_sec"] == 1800
    assert payload["target"]["rows"] == [
        {"ticker": "KRW", "name": "현금", "weight": 0.3},
        {"ticker": "mid", "name": "중기 블록", "weight": 0.3},
        {"ticker": "core_etf", "name": "ETF/Core", "weight": 0.1},
    ]
