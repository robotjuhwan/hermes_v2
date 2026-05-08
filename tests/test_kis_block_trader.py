from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tradecraft.services.kis_block_trader import (
    KISBlockTrader,
    KISBlockTraderConfig,
    aggressive_limit_price,
)


class _FakeKIS:
    def __init__(self) -> None:
        self.orders: list[dict] = []
        self.order_daily: dict[str, dict] = {}
        self.canceled_orders: list[dict] = []
        self.cash = 10_000_000.0
        self.positions: dict[str, int] = {}
        self.prices: dict[str, float] = {"277810": 100_000.0, "005930": 76_000.0}

    async def fetch_balance_assets(self) -> list[dict]:
        rows: list[dict] = [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": self.cash,
                "available": self.cash,
                "value_krw": self.cash,
            }
        ]
        for symbol, qty in self.positions.items():
            price = self.prices.get(symbol, 0.0)
            rows.append(
                {
                    "asset": symbol,
                    "asset_name": "레인보우로보틱스" if symbol == "277810" else symbol,
                    "kind": "position",
                    "qty": qty,
                    "available": qty,
                    "avg_price": price,
                    "mark_price": price,
                    "value_krw": price * qty,
                    "pnl_krw": 0,
                }
            )
        return rows

    async def fetch_domestic_quote(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "name": "레인보우로보틱스" if symbol == "277810" else symbol,
            "price": self.prices.get(symbol, 0.0),
            "raw": {
                "stck_prpr": str(int(self.prices.get(symbol, 0.0))),
                "hts_kor_isnm": "레인보우로보틱스" if symbol == "277810" else symbol,
            },
        }

    async def submit_domestic_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: int = 0,
        order_type: str = "00",
    ) -> dict:
        row = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "order_no": f"O{len(self.orders) + 1}",
            "order_orgno": "00123",
        }
        self.orders.append(row)
        if side == "buy":
            self.positions[symbol] = self.positions.get(symbol, 0) + quantity
            self.cash -= quantity * price
        else:
            self.positions[symbol] = max(self.positions.get(symbol, 0) - quantity, 0)
            self.cash += quantity * price
        return row

    async def fetch_domestic_order_daily(
        self,
        *,
        symbol: str = "",
        order_no: str = "",
        order_orgno: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
        side_code: str = "00",
        ccld_dvsn: str = "00",
        max_pages: int = 3,
    ) -> dict:
        _ = (symbol, order_orgno, start_date, end_date, side_code, ccld_dvsn, max_pages)
        return self.order_daily.get(order_no, {"status": "ok", "orders": []})

    async def cancel_domestic_order(
        self,
        *,
        order_no: str,
        order_orgno: str = "",
        quantity: int = 0,
        order_type: str = "00",
        exchange_id: str = "",
    ) -> dict:
        row = {
            "order_no": order_no,
            "order_orgno": order_orgno,
            "quantity": quantity,
            "order_type": order_type,
            "exchange_id": exchange_id,
            "cancel_order_no": f"C{len(self.canceled_orders) + 1}",
        }
        self.canceled_orders.append(row)
        return row


class _FakeLLM:
    ready = True
    resolved_model = "gpt-5.5"

    def __init__(self, content: dict) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def complete(self, payload, timeout_ms=None) -> dict:
        _ = (payload, timeout_ms)
        self.calls.append(payload)
        return {"ok": True, "content": json.dumps(self.content, ensure_ascii=False)}


class _FakeStrategy:
    def build_candidates(self, *, query, research_feed, limit=None) -> dict:
        _ = (query, research_feed, limit)
        return {
            "status": "ok",
            "candidates": [
                {
                    "symbol": "277810",
                    "name": "레인보우로보틱스",
                    "score": 76,
                    "confidence": 72,
                    "risk_score": 28,
                    "sources": ["naver_reports", "after_close_330"],
                    "data_coverage": {"source_count": 2},
                    "identity_status": {"status": "ok"},
                    "suitability": {"balanced": {"score": 76, "grade": "B"}},
                }
            ],
        }


def _trader(tmp_path: Path, *, execute_orders: bool = False, llm_payload: dict | None = None) -> KISBlockTrader:
    return KISBlockTrader(
        config=KISBlockTraderConfig(
            db_path=str(tmp_path / "kis_blocks.db"),
            execute_orders=execute_orders,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        llm_bridge=_FakeLLM(llm_payload or {}),  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
    )


def test_aggressive_limit_price_rounds_to_krx_tick() -> None:
    assert aggressive_limit_price(100_000, side="buy", bps=30) == 100_500
    assert aggressive_limit_price(100_000, side="sell", bps=30) == 99_700


def test_manager_creates_independent_same_symbol_blocks(tmp_path: Path) -> None:
    trader = _trader(
        tmp_path,
        llm_payload={
            "create_blocks": [
                {
                    "symbol": "277810",
                    "qty": 1,
                    "target_price": 110_000,
                    "stop_price": 95_000,
                    "thesis": "1주 블록",
                    "confidence": 0.8,
                },
                {
                    "symbol": "277810",
                    "qty": 2,
                    "target_price": 112_000,
                    "stop_price": 94_000,
                    "thesis": "2주 블록",
                    "confidence": 0.78,
                },
            ]
        },
    )
    trader.clock = lambda: {"session": "regular", "is_market_open": True}  # type: ignore[method-assign]

    result = asyncio.run(trader.run_manager_once())
    blocks = trader.repository.list_blocks()

    assert result["status"] == "ok"
    assert len(blocks) == 2
    assert {row["qty_initial"] for row in blocks} == {1, 2}
    assert len({row["block_id"] for row in blocks}) == 2


def test_manager_prompt_includes_investment_memory_context(tmp_path: Path) -> None:
    llm = _FakeLLM({"create_blocks": []})
    trader = KISBlockTrader(
        config=KISBlockTraderConfig(
            db_path=str(tmp_path / "kis_blocks.db"),
            execute_orders=False,
            use_naver_fallback=False,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        llm_bridge=llm,  # type: ignore[arg-type]
        strategy_engine=_FakeStrategy(),  # type: ignore[arg-type]
        memory_context_provider=lambda **_: {
            "status": "ok",
            "persona": "친근한 투자 파트너",
            "active_policies": [{"policy_id": "avoid_chasing"}],
        },
    )
    trader.clock = lambda: {"session": "regular", "is_market_open": True}  # type: ignore[method-assign]

    result = asyncio.run(trader.run_manager_once())
    prompt = json.loads(llm.calls[0]["messages"][1]["content"])

    assert result["status"] == "ok"
    assert prompt["investment_memory"]["persona"] == "친근한 투자 파트너"
    assert "adopt_existing_blocks" in prompt["output_schema"]
    assert prompt["policy"]["memory_guard"].startswith("investment memory")


def test_adoption_run_assigns_existing_position_without_buy_order(tmp_path: Path) -> None:
    trader = _trader(
        tmp_path,
        llm_payload={
            "adopt_existing_blocks": [
                {
                    "symbol": "277810",
                    "qty": 2,
                    "target_price": 112_000,
                    "stop_price": 95_000,
                    "thesis": "기존 보유 2주를 쥬가 관리 블록으로 흡수",
                    "confidence": 0.72,
                    "risk_note": "목표/손절 약속 우선",
                }
            ]
        },
    )
    trader.kis.positions["277810"] = 2  # type: ignore[attr-defined]
    trader.clock = lambda: {"session": "closed", "is_market_open": False}  # type: ignore[method-assign]

    result = asyncio.run(trader.run_adoption_once())
    blocks = trader.repository.list_blocks()
    events = trader.repository.list_events(block_id=blocks[0]["block_id"])

    assert result["status"] == "ok"
    assert result["actions"]["adopt_existing_blocks"][0]["qty"] == 2
    assert result["applied"]["adopted"][0]["status"] == "ok"
    assert len(blocks) == 1
    assert blocks[0]["status"] == "open"
    assert blocks[0]["created_by"] == "existing_position"
    assert blocks[0]["qty_open"] == 2
    assert trader.kis.orders == []  # type: ignore[attr-defined]
    assert events[0]["event_type"] == "adopted_existing_position"


def test_adoption_does_not_overallocate_existing_position(tmp_path: Path) -> None:
    trader = _trader(
        tmp_path,
        llm_payload={
            "adopt_existing_blocks": [
                {
                    "symbol": "277810",
                    "qty": 3,
                    "target_price": 112_000,
                    "stop_price": 95_000,
                    "thesis": "초과 배정 시도",
                    "confidence": 0.72,
                }
            ]
        },
    )
    trader.kis.positions["277810"] = 2  # type: ignore[attr-defined]
    trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 1,
            "entry_price": 100_000,
            "target_price": 110_000,
            "stop_price": 95_000,
            "status": "open",
        }
    )

    result = asyncio.run(trader.run_adoption_once())
    blocks = trader.repository.list_blocks()

    assert result["actions"]["adopt_existing_blocks"] == []
    assert result["applied"]["adopted"] == []
    assert len(blocks) == 1


def test_rule_executor_closes_block_without_llm_when_target_reached(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "name": "레인보우로보틱스",
            "qty": 1,
            "qty_open": 1,
            "entry_price": 100_000,
            "target_price": 101_000,
            "stop_price": 95_000,
            "status": "open",
            "opened_at": "2026-05-07T00:00:00+00:00",
        }
    )
    trader.kis.prices["277810"] = 102_000  # type: ignore[attr-defined]

    tick = asyncio.run(trader.executor_tick(manual=True))
    updated = trader.repository.get_block(block["block_id"])

    assert tick["action_count"] == 1
    assert updated is not None
    assert updated["status"] == "closed"
    assert trader.repository.list_orders(block["block_id"])[0]["side"] == "sell"


def test_pending_block_does_not_duplicate_exit_order(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 1,
            "entry_price": 100_000,
            "target_price": 101_000,
            "stop_price": 95_000,
            "status": "exit_pending",
        }
    )
    trader.kis.prices["277810"] = 102_000  # type: ignore[attr-defined]

    tick = asyncio.run(trader.executor_tick(manual=True))

    assert tick["action_count"] == 0
    assert trader.repository.list_orders(block["block_id"]) == []


def test_live_entry_pending_opens_from_order_inquiry(tmp_path: Path) -> None:
    trader = _trader(tmp_path, execute_orders=True)
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 0,
            "entry_price": 100_000,
            "target_price": 110_000,
            "stop_price": 95_000,
            "status": "entry_pending",
        }
    )
    order = trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "277810",
            "side": "buy",
            "qty": 1,
            "limit_price": 100_500,
            "order_type": "00",
            "status": "sent",
            "order_no": "O1",
            "order_orgno": "00123",
        }
    )
    trader.kis.order_daily["O1"] = {  # type: ignore[attr-defined]
        "status": "ok",
        "orders": [
            {
                "order_no": "O1",
                "order_orgno": "00123",
                "symbol": "277810",
                "filled_qty": 1,
                "remaining_qty": 0,
                "avg_fill_price": 100_500,
                "raw": {"tot_ccld_qty": "1"},
            }
        ],
    }

    tick = asyncio.run(trader.executor_tick(manual=True))
    updated_block = trader.repository.get_block(block["block_id"])
    updated_order = trader.repository.get_order(order["id"])

    assert tick["order_reconciliation"]["change_count"] == 1
    assert updated_block is not None
    assert updated_block["status"] == "open"
    assert updated_block["qty_open"] == 1
    assert updated_order is not None
    assert updated_order["status"] == "filled"
    assert updated_order["filled_qty"] == 1


def test_stale_live_order_requests_cancel_without_duplicate_order(tmp_path: Path) -> None:
    trader = _trader(tmp_path, execute_orders=True)
    trader.config.pending_reconcile_timeout_sec = 30
    block = trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 0,
            "entry_price": 100_000,
            "target_price": 110_000,
            "stop_price": 95_000,
            "status": "entry_pending",
        }
    )
    order = trader.repository.add_order(
        {
            "block_id": block["block_id"],
            "symbol": "277810",
            "side": "buy",
            "qty": 1,
            "limit_price": 100_500,
            "order_type": "00",
            "status": "sent",
            "order_no": "O1",
            "order_orgno": "00123",
        }
    )
    with trader.repository._connect() as conn:
        conn.execute(
            "UPDATE block_orders SET created_at = ? WHERE id = ?",
            ("2026-05-07T00:00:00+00:00", order["id"]),
        )

    tick = asyncio.run(trader.executor_tick(manual=True))
    updated_order = trader.repository.get_order(order["id"])

    assert tick["order_reconciliation"]["change_count"] == 1
    assert updated_order is not None
    assert updated_order["status"] == "cancel_requested"
    assert updated_order["cancel_requested"] is True
    assert trader.kis.canceled_orders == [  # type: ignore[attr-defined]
        {
            "order_no": "O1",
            "order_orgno": "00123",
            "quantity": 0,
            "order_type": "00",
            "exchange_id": "",
            "cancel_order_no": "C1",
        }
    ]


def test_allocation_reports_unallocated_and_overallocated_qty(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.kis.positions["277810"] = 3  # type: ignore[attr-defined]
    trader.repository.create_block(
        {
            "symbol": "277810",
            "qty": 1,
            "qty_open": 1,
            "entry_price": 100_000,
            "target_price": 110_000,
            "stop_price": 95_000,
            "status": "open",
        }
    )

    snapshot = asyncio.run(trader.snapshot())
    row = snapshot["allocation"]["items"][0]

    assert row["account_qty"] == 3
    assert row["block_qty"] == 1
    assert row["unallocated_qty"] == 2
    assert row["overallocated_qty"] == 0
