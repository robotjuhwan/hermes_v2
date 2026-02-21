from __future__ import annotations

import asyncio
from pathlib import Path

from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.kis_llm_trader import KISLLMTrader, KISLLMTraderConfig


class _FakeKIS:
    def __init__(self) -> None:
        self.orders: list[dict] = []

    async def fetch_balance_assets(self) -> list[dict]:
        return [
            {
                "asset": "KRW",
                "kind": "cash",
                "qty": 1_000_000.0,
                "available": 1_000_000.0,
                "value_krw": 1_000_000.0,
            }
        ]

    async def fetch_domestic_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "name": symbol, "price": 50_000.0}

    async def submit_domestic_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: int = 0,
        order_type: str = "01",
    ) -> dict:
        row = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "order_no": "A1",
        }
        self.orders.append(row)
        return row


def test_kis_llm_trader_runs_with_fallback(tmp_path: Path, monkeypatch) -> None:
    research_path = tmp_path / "research.json"
    trader_path = tmp_path / "kis_trader.json"
    RuntimeStateStore(research_path).write_snapshot(
        {
            "updated_at": utc_now_iso(),
            "source": "research_runner",
            "query": "KRX",
            "items": [
                {
                    "title": "note",
                    "summary": "관심종목 005930, 000660",
                    "picks": ["005930", "000660"],
                }
            ],
        }
    )

    fake = _FakeKIS()
    trader = KISLLMTrader(
        config=KISLLMTraderConfig(
            research_state_path=str(research_path),
            trader_state_path=str(trader_path),
            llm_command="",
            persona="pro",
            max_orders_per_cycle=2,
            max_budget_per_order_krw=200_000,
            min_confidence=0.5,
            default_order_type="01",
            allow_sell=True,
            max_candidate_codes=10,
        ),
        kis=fake,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(trader, "_is_krx_open", lambda: True)

    snapshot = asyncio.run(trader.run_once())
    assert snapshot["status"] == "ok"
    assert len(fake.orders) >= 1
    assert trader_path.exists()


def test_collect_report_context_ranks_recency(tmp_path: Path) -> None:
    research_path = tmp_path / "research.json"
    trader_path = tmp_path / "kis_trader.json"
    RuntimeStateStore(research_path).write_snapshot(
        {
            "updated_at": utc_now_iso(),
            "source": "research_runner",
            "query": "KRX",
            "items": [],
        }
    )

    class _Repo:
        def search(self, query: str, symbol: str = "", limit: int = 10) -> list[dict]:
            _ = (query, symbol, limit)
            return [
                {
                    "report_id": 1,
                    "title": "old",
                    "snippet": "old",
                    "published_at": "2026-02-10",
                },
                {
                    "report_id": 2,
                    "title": "new",
                    "snippet": "new",
                    "published_at": "2026-02-17",
                },
            ]

    trader = KISLLMTrader(
        config=KISLLMTraderConfig(
            research_state_path=str(research_path),
            trader_state_path=str(trader_path),
            llm_command="",
            persona="pro",
            report_context_top_k=1,
        ),
        kis=_FakeKIS(),  # type: ignore[arg-type]
        report_repo=_Repo(),  # type: ignore[arg-type]
    )

    rows = trader._collect_report_context(["005930"], {"query": "KRX"})
    assert len(rows) == 1
    assert rows[0]["report_id"] == 2


def test_kis_llm_trader_uses_target_symbols_from_trader_state(tmp_path: Path) -> None:
    research_path = tmp_path / "research.json"
    trader_path = tmp_path / "kis_trader.json"
    RuntimeStateStore(research_path).write_snapshot(
        {
            "updated_at": utc_now_iso(),
            "source": "research_runner",
            "query": "KRX",
            "items": [{"title": "note", "summary": "no picks", "picks": []}],
        }
    )
    RuntimeStateStore(trader_path).write_snapshot(
        {
            "updated_at": utc_now_iso(),
            "target_symbols": ["005930", "000660"],
        }
    )

    fake = _FakeKIS()
    trader = KISLLMTrader(
        config=KISLLMTraderConfig(
            research_state_path=str(research_path),
            trader_state_path=str(trader_path),
            llm_command="",
            persona="pro",
            max_orders_per_cycle=2,
            max_budget_per_order_krw=200_000,
            min_confidence=0.5,
            default_order_type="01",
            allow_sell=True,
            max_candidate_codes=10,
        ),
        kis=fake,  # type: ignore[arg-type]
    )
    trader._is_krx_open = lambda: True  # type: ignore[method-assign]

    snapshot = asyncio.run(trader.run_once())
    assert snapshot["status"] == "ok"
    assert snapshot.get("target_symbols") == ["005930", "000660"]
    assert list(snapshot.get("candidates") or [])[:2] == ["005930", "000660"]
