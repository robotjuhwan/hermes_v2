from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.services.binance_execution_defects import (
    execution_defect_reasons_for_row,
    execution_defect_risk_from_rows,
    partition_performance_rows,
    row_has_invalid_price_geometry,
    row_is_malformed_market_scope_execution,
    row_is_reconciliation_only_close,
)

ROOT = Path(__file__).resolve().parents[1]


def _row(**overrides: Any) -> sqlite3.Row:
    values: dict[str, Any] = {
        "block_id": "block-1",
        "symbol": "BTCUSDT",
        "market": "spot",
        "side": "long",
        "lane": "spot:long",
        "entry_price": 100.0,
        "target_price": 110.0,
        "stop_price": 95.0,
        "lesson_json": "{}",
        "block_metadata_json": "{}",
        "block_market": "spot",
        "block_thesis": "",
        "block_llm_reason": "",
        "pnl_usdt": 0.0,
        "net_pnl_usdt": 0.0,
    }
    values.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    select_parts = ", ".join(f'? AS "{key}"' for key in values)
    return conn.execute(
        f"SELECT {select_parts}",
        tuple(values.values()),
    ).fetchone()


def test_execution_defect_row_classifiers_detect_geometry_reconciliation_and_scope() -> None:
    invalid_geometry = _row(target_price=90.0)
    reconciliation = _row(
        block_metadata_json=json.dumps(
            {"exit_reconciled_missing_asset": {"asset": "BTC"}}
        )
    )
    malformed_scope = _row(block_metadata_json=json.dumps({"horizon": "futures"}))

    assert row_has_invalid_price_geometry(invalid_geometry)
    assert row_is_reconciliation_only_close(reconciliation)
    assert row_is_malformed_market_scope_execution(malformed_scope)
    assert execution_defect_reasons_for_row(invalid_geometry) == [
        "invalid_price_geometry"
    ]


def test_partition_performance_rows_excludes_defective_rows() -> None:
    clean = _row()
    invalid_geometry = _row(block_id="bad-1", target_price=90.0)
    reconciliation = _row(
        block_id="bad-2",
        block_metadata_json=json.dumps(
            {"exit_reconciled_missing_asset": {"asset": "ETH"}}
        ),
    )

    clean_rows, excluded_rows = partition_performance_rows(
        [clean, invalid_geometry, reconciliation]
    )

    assert [row["block_id"] for row in clean_rows] == ["block-1"]
    assert [row["block_id"] for row in excluded_rows] == ["bad-1", "bad-2"]


def test_execution_defect_risk_summarizes_reasons_scope_and_examples() -> None:
    invalid_geometry = _row(block_id="bad-1", target_price=90.0, net_pnl_usdt=-2.5)
    reconciliation = _row(
        block_id="bad-2",
        symbol="ETHUSDT",
        net_pnl_usdt=1.0,
        block_metadata_json=json.dumps(
            {"exit_reconciled_missing_asset": {"asset": "ETH"}}
        ),
    )

    risk = execution_defect_risk_from_rows([invalid_geometry, reconciliation])

    assert risk["status"] == "elevated"
    assert risk["excluded_count"] == 2
    assert risk["excluded_loss_usdt"] == 2.5
    assert risk["excluded_gain_usdt"] == 1.0
    assert risk["reasons"]["invalid_price_geometry"] == 1
    assert risk["reasons"]["reconciliation_only_close"] == 1
    assert risk["scope_counts"]["spot:long"] == 2
    assert risk["examples"][0]["block_id"] == "bad-1"


def test_binance_execution_defect_helpers_live_outside_block_trader() -> None:
    trader_source = (
        ROOT / "src/tradecraft/services/binance_block_trader.py"
    ).read_text()
    helper_source = (
        ROOT / "src/tradecraft/services/binance_execution_defects.py"
    ).read_text()

    assert "def partition_performance_rows(" in helper_source
    assert "def execution_defect_risk_from_rows(" in helper_source
    assert "def execution_defect_reasons_for_row(" in helper_source
    assert "def row_has_invalid_price_geometry(" in helper_source
    assert "def row_is_reconciliation_only_close(" in helper_source
    assert "def row_is_malformed_market_scope_execution(" in helper_source
    assert "def _partition_performance_rows(" not in trader_source
    assert "def _execution_defect_risk_from_rows(" not in trader_source
    assert "def _execution_defect_reasons_for_row(" not in trader_source
    assert "def _row_has_invalid_price_geometry(" not in trader_source
    assert "def _row_is_reconciliation_only_close(" not in trader_source
    assert "def _row_is_malformed_market_scope_execution(" not in trader_source
