# Binance Jue Branch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Binance spot+futures trading branch for Jue that uses shared Jue memory/intelligence, GPT-5.3-Codex-Spark for 24-hour lightweight decisions, rule-based block execution, and a dedicated UI tab.

**Architecture:** Keep Jue core memory/persona shared, but do not extend `KISBlockTrader` directly. Create a separate `BinanceBlockTrader` with its own DB, runner, API routes, and UI tab because Binance is 24/7 and futures require liquidation/leverage gates that KIS does not have. The LLM manager creates and adjusts blocks; the rule executor watches prices and executes target/stop/trailing/time-stop rules without LLM calls.

**Tech Stack:** Python 3.10+, FastAPI, sqlite3, httpx Binance REST, static HTML/CSS/JS frontend, pytest, existing `CodexNativeRuntime`, existing `BinanceAdapter`, existing `InvestmentMemoryService`.

---

## Scope And Non-Negotiables

- Binance spot and USD-M futures are both included in v1.
- Live execution is split:
  - `TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS=false` by default.
  - `TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS=false` by default.
- Futures v1 supports isolated margin only. Cross margin is rejected.
- Futures leverage is capped by config and by liquidation-distance checks.
- GPT-5.3-Codex-Spark is used for Binance manager calls by default:
  - `TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL=gpt-5.3-codex-spark`
  - `TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_REASONING_EFFORT=high`
- gpt-5.5 remains available for daily/weekly deep memory review through the existing memory loop.
- The first working version can run in paper mode and still generate blocks, rule actions, history, and UI state.
- No Binance order is sent unless the matching spot/futures execution flag is enabled and kill switch is released.

## File Structure

- Create `src/tradecraft/services/binance_block_trader.py`
  - Owns Binance block DB schema, block manager prompt, rule executor, reconciliation, snapshots, and public service methods.
- Modify `src/tradecraft/services/binance.py`
  - Add public quote, exchange filter, order, open order, futures position, funding/open-interest helpers needed by `BinanceBlockTrader`.
- Create `src/tradecraft/runtime/binance_block_trader_runner.py`
  - 24-hour runner: quote/rule tick frequently, Spark manager less frequently.
- Modify `src/tradecraft/config.py`
  - Add Binance block trader settings.
- Modify `src/tradecraft/runtime/process_status.py`
  - Add runner status key for readiness.
- Modify `src/tradecraft/main.py`
  - Instantiate `BinanceBlockTrader` and add admin-protected `/api/binance/blocks/**` routes.
- Modify `src/tradecraft/web/static/index.html`
  - Add helper tab button for `binance_trader`.
- Modify `src/tradecraft/web/static/app.js`
  - Add state, loaders, renderer, event handlers for Binance trading tab.
- Modify `src/tradecraft/web/static/style.css`
  - Add responsive Binance block board styles using existing dark theme tokens.
- Modify `src/tradecraft/services/settings_catalog.py`
  - Add Binance block trader settings to the operating settings page.
- Modify `.env.example`
  - Document non-secret defaults.
- Tests:
  - Create `tests/test_binance_block_trader.py`
  - Create `tests/test_binance_block_trader_runner.py`
  - Modify `tests/test_binance_adapter.py`
  - Modify `tests/test_kis_trader_api.py` or create `tests/test_binance_trader_api.py`
  - Modify `tests/test_config.py`
  - Modify `tests/test_api_smoke.py`

---

## Task 1: Add Binance Block Trader Configuration

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `src/tradecraft/services/settings_catalog.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing config test**

Add to `tests/test_config.py`:

```python
def test_binance_block_trader_defaults() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.binance_block_trader_enabled is False
    assert settings.binance_block_trader_execute_spot_orders is False
    assert settings.binance_block_trader_execute_futures_orders is False
    assert settings.binance_block_trader_db_path == ".runtime/binance_blocks.db"
    assert settings.binance_block_trader_state_path == ".runtime/binance_block_trader.json"
    assert settings.binance_block_trader_quote_interval_sec == 15
    assert settings.binance_block_trader_rule_interval_sec == 15
    assert settings.binance_block_trader_manager_interval_sec == 1800
    assert settings.binance_block_trader_llm_model == "gpt-5.3-codex-spark"
    assert settings.binance_block_trader_llm_reasoning_effort == "high"
    assert settings.binance_block_trader_max_manager_symbols == 12
    assert settings.binance_block_trader_spot_universe == "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
    assert settings.binance_block_trader_futures_universe == "BTCUSDT,ETHUSDT,SOLUSDT"
    assert settings.binance_block_trader_max_futures_leverage == 2
    assert settings.binance_block_trader_min_liquidation_distance_pct == 12.0
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest tests/test_config.py::test_binance_block_trader_defaults -q
```

Expected: FAIL with `AttributeError` for `binance_block_trader_enabled`.

- [ ] **Step 3: Add settings fields**

Add these fields to `AppSettings` in `src/tradecraft/config.py` near existing Binance/KIS trader fields:

```python
    binance_block_trader_enabled: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED",
    )
    binance_block_trader_execute_spot_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS",
    )
    binance_block_trader_execute_futures_orders: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS",
    )
    binance_block_trader_once: bool = Field(
        default=False,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_ONCE",
    )
    binance_block_trader_db_path: str = Field(
        default=".runtime/binance_blocks.db",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_DB_PATH",
    )
    binance_block_trader_state_path: str = Field(
        default=".runtime/binance_block_trader.json",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_STATE_PATH",
    )
    binance_block_trader_quote_interval_sec: int = Field(
        default=15,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_QUOTE_INTERVAL_SEC",
    )
    binance_block_trader_rule_interval_sec: int = Field(
        default=15,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_RULE_INTERVAL_SEC",
    )
    binance_block_trader_manager_interval_sec: int = Field(
        default=1800,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MANAGER_INTERVAL_SEC",
    )
    binance_block_trader_llm_model: str = Field(
        default="gpt-5.3-codex-spark",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL",
    )
    binance_block_trader_llm_reasoning_effort: str = Field(
        default="high",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_REASONING_EFFORT",
    )
    binance_block_trader_max_manager_symbols: int = Field(
        default=12,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_MANAGER_SYMBOLS",
    )
    binance_block_trader_spot_universe: str = Field(
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_UNIVERSE",
    )
    binance_block_trader_futures_universe: str = Field(
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_UNIVERSE",
    )
    binance_block_trader_max_futures_leverage: int = Field(
        default=2,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_FUTURES_LEVERAGE",
    )
    binance_block_trader_min_liquidation_distance_pct: float = Field(
        default=12.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_LIQUIDATION_DISTANCE_PCT",
    )
    binance_block_trader_aggressive_limit_bps: float = Field(
        default=20.0,
        alias="TRADECRAFT_BINANCE_BLOCK_TRADER_AGGRESSIVE_LIMIT_BPS",
    )
```

- [ ] **Step 4: Add settings catalog metadata**

Add `META` entries in `src/tradecraft/services/settings_catalog.py`:

```python
    "binance_block_trader_enabled": SettingMeta(
        "바이낸스 쥬 브랜치",
        "바이낸스 현물/선물 블록 트레이딩 루프를 켭니다.",
        "trading",
    ),
    "binance_block_trader_execute_spot_orders": SettingMeta(
        "바이낸스 현물 실주문",
        "켜면 현물 블록 주문이 Binance spot 주문으로 나갑니다.",
        "trading",
        risk="danger",
    ),
    "binance_block_trader_execute_futures_orders": SettingMeta(
        "바이낸스 선물 실주문",
        "켜면 선물 블록 주문이 Binance USD-M futures 주문으로 나갑니다.",
        "trading",
        risk="danger",
    ),
    "binance_block_trader_llm_model": SettingMeta(
        "바이낸스 판단 모델",
        "24시간 바이낸스 매니저 판단에 쓰는 모델입니다.",
        "ai",
        choices=("gpt-5.3-codex-spark", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
    ),
    "binance_block_trader_llm_reasoning_effort": SettingMeta(
        "바이낸스 추론 강도",
        "바이낸스 매니저 호출의 reasoning effort입니다.",
        "ai",
        choices=("low", "medium", "high", "xhigh"),
    ),
    "binance_block_trader_spot_universe": SettingMeta(
        "바이낸스 현물 유니버스",
        "쥬가 현물 블록 후보로 보는 심볼 목록입니다.",
        "trading",
        input_type="textarea",
    ),
    "binance_block_trader_futures_universe": SettingMeta(
        "바이낸스 선물 유니버스",
        "쥬가 선물 블록 후보로 보는 심볼 목록입니다.",
        "trading",
        input_type="textarea",
    ),
```

Also add both execution flags to `HIGH_RISK_FIELDS`.

- [ ] **Step 5: Add `.env.example` defaults**

Append these lines near existing Binance config:

```bash
TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED=false
TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS=false
TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS=false
TRADECRAFT_BINANCE_BLOCK_TRADER_QUOTE_INTERVAL_SEC=15
TRADECRAFT_BINANCE_BLOCK_TRADER_RULE_INTERVAL_SEC=15
TRADECRAFT_BINANCE_BLOCK_TRADER_MANAGER_INTERVAL_SEC=1800
TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL=gpt-5.3-codex-spark
TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_REASONING_EFFORT=high
TRADECRAFT_BINANCE_BLOCK_TRADER_SPOT_UNIVERSE=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT
TRADECRAFT_BINANCE_BLOCK_TRADER_FUTURES_UNIVERSE=BTCUSDT,ETHUSDT,SOLUSDT
TRADECRAFT_BINANCE_BLOCK_TRADER_MAX_FUTURES_LEVERAGE=2
TRADECRAFT_BINANCE_BLOCK_TRADER_MIN_LIQUIDATION_DISTANCE_PCT=12.0
```

- [ ] **Step 6: Verify**

Run:

```bash
pytest tests/test_config.py::test_binance_block_trader_defaults -q
ruff check src/tradecraft/config.py src/tradecraft/services/settings_catalog.py tests/test_config.py
```

Expected: PASS and `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/config.py src/tradecraft/services/settings_catalog.py .env.example tests/test_config.py
git commit -m "feat: add binance block trader settings"
```

---

## Task 2: Extend Binance Adapter For Trading And Market Context

**Files:**
- Modify: `src/tradecraft/services/binance.py`
- Modify: `tests/test_binance_adapter.py`

- [ ] **Step 1: Write failing adapter tests**

Add tests:

```python
def test_binance_spot_limit_order_payload(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_post_spot(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        return {"orderId": 123, "status": "NEW", "symbol": "BTCUSDT"}

    monkeypatch.setattr(adapter, "_signed_post_spot", fake_signed_post_spot)
    result = asyncio.run(
        adapter.submit_spot_order(
            symbol="BTCUSDT",
            side="BUY",
            quantity=0.01,
            limit_price=100000.0,
            client_order_id="block-1-entry",
        )
    )

    assert captured["path"] == "/api/v3/order"
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["side"] == "BUY"
    assert captured["params"]["type"] == "LIMIT"
    assert captured["params"]["timeInForce"] == "IOC"
    assert captured["params"]["quantity"] == "0.01000000"
    assert captured["params"]["price"] == "100000.00000000"
    assert captured["params"]["newClientOrderId"] == "block-1-entry"
    assert result["order_id"] == "123"


def test_binance_futures_reduce_only_close_payload(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))
    captured = {}

    async def fake_signed_post_futures(path: str, params: dict) -> dict:
        captured["path"] = path
        captured["params"] = params
        return {"orderId": 456, "status": "NEW", "symbol": "ETHUSDT"}

    monkeypatch.setattr(adapter, "_signed_post_futures", fake_signed_post_futures)
    result = asyncio.run(
        adapter.submit_futures_order(
            symbol="ETHUSDT",
            side="BUY",
            quantity=0.2,
            limit_price=3000.0,
            client_order_id="block-2-close",
            reduce_only=True,
        )
    )

    assert captured["path"] == "/fapi/v1/order"
    assert captured["params"]["reduceOnly"] == "true"
    assert captured["params"]["timeInForce"] == "IOC"
    assert result["order_id"] == "456"


def test_binance_futures_position_risk_mapping(monkeypatch) -> None:
    adapter = BinanceAdapter(BinanceConfig(spot_api_key="k", spot_api_secret="s"))

    async def fake_signed_get_futures(path: str, params: dict) -> list[dict]:
        assert path == "/fapi/v2/positionRisk"
        return [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.01",
                "entryPrice": "90000",
                "markPrice": "100000",
                "unRealizedProfit": "100",
                "liquidationPrice": "70000",
                "leverage": "2",
                "marginType": "isolated",
            }
        ]

    monkeypatch.setattr(adapter, "_signed_get_futures", fake_signed_get_futures)
    rows = asyncio.run(adapter.fetch_futures_position_risk())

    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["position_amt"] == 0.01
    assert rows[0]["liquidation_price"] == 70000.0
    assert rows[0]["margin_type"] == "isolated"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_binance_adapter.py::test_binance_spot_limit_order_payload tests/test_binance_adapter.py::test_binance_futures_reduce_only_close_payload tests/test_binance_adapter.py::test_binance_futures_position_risk_mapping -q
```

Expected: FAIL with missing methods.

- [ ] **Step 3: Add order and futures risk methods**

Add these methods to `BinanceAdapter`:

```python
    async def submit_spot_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        client_order_id: str,
    ) -> dict[str, Any]:
        payload = await self._signed_post_spot(
            "/api/v3/order",
            {
                "symbol": symbol.upper().strip(),
                "side": side.upper().strip(),
                "type": "LIMIT",
                "timeInForce": "IOC",
                "quantity": self._format_decimal(quantity),
                "price": self._format_decimal(limit_price),
                "newClientOrderId": client_order_id[:36],
            },
        )
        return self._normalize_order_response(payload)

    async def submit_futures_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        client_order_id: str,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        payload = await self._signed_post_futures(
            "/fapi/v1/order",
            {
                "symbol": symbol.upper().strip(),
                "side": side.upper().strip(),
                "type": "LIMIT",
                "timeInForce": "IOC",
                "quantity": self._format_decimal(quantity),
                "price": self._format_decimal(limit_price),
                "newClientOrderId": client_order_id[:36],
                "reduceOnly": "true" if reduce_only else "false",
            },
        )
        return self._normalize_order_response(payload)

    async def fetch_futures_position_risk(self) -> list[dict[str, Any]]:
        payload = await self._signed_get_futures("/fapi/v2/positionRisk", {})
        if not isinstance(payload, list):
            raise BinanceAPIError("binance futures position risk malformed")
        rows: list[dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "position_amt": self._to_float(row.get("positionAmt")),
                    "entry_price": self._to_float(row.get("entryPrice")),
                    "mark_price": self._to_float(row.get("markPrice")),
                    "unrealized_profit": self._to_float(
                        row.get("unRealizedProfit") or row.get("unrealizedProfit")
                    ),
                    "liquidation_price": self._to_float(row.get("liquidationPrice")),
                    "leverage": int(self._to_float(row.get("leverage"))),
                    "margin_type": str(row.get("marginType") or "").lower().strip(),
                }
            )
        return rows
```

Also add:

```python
    async def _signed_post_spot(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        signed = self._sign_params(
            params=params,
            secret=self.config.spot_api_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        url = f"{self.config.spot_base_url.rstrip('/')}{path}"
        headers = {"X-MBX-APIKEY": self.config.spot_api_key, "Accept": "application/json"}
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, params=signed, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance spot post failed: {payload}")
        return payload

    async def _signed_post_futures(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        signed = self._sign_params(
            params=params,
            secret=self.config.futures_secret,
            recv_window_ms=self.config.recv_window_ms,
        )
        url = f"{self.config.futures_base_url.rstrip('/')}{path}"
        headers = {"X-MBX-APIKEY": self.config.futures_key, "Accept": "application/json"}
        timeout = httpx.Timeout(8.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, params=signed, headers=headers)
        payload = self._parse_json(response)
        if response.status_code >= 400:
            raise BinanceAPIError(f"binance futures post failed: {payload}")
        return payload

    @staticmethod
    def _format_decimal(value: float) -> str:
        return f"{float(value):.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def _normalize_order_response(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": str(payload.get("orderId") or ""),
            "client_order_id": str(payload.get("clientOrderId") or ""),
            "symbol": str(payload.get("symbol") or ""),
            "status": str(payload.get("status") or ""),
            "raw": payload,
        }
```

- [ ] **Step 4: Verify**

```bash
pytest tests/test_binance_adapter.py -q
ruff check src/tradecraft/services/binance.py tests/test_binance_adapter.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/binance.py tests/test_binance_adapter.py
git commit -m "feat: add binance order adapter methods"
```

---

## Task 3: Create Binance Block Trader DB And Snapshot Service

**Files:**
- Create: `src/tradecraft/services/binance_block_trader.py`
- Create: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing DB initialization test**

Create `tests/test_binance_block_trader.py`:

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tradecraft.services.binance_block_trader import (
    BinanceBlockTrader,
    BinanceBlockTraderConfig,
)


class FakeBinance:
    async def fetch_spot_assets(self, usdt_krw_rate: float | None = None) -> list[dict]:
        return [
            {"asset": "USDT", "kind": "cash", "qty": 1000, "available": 1000, "value_krw": 1_400_000, "pnl_krw": 0},
            {"asset": "BTC", "kind": "position", "qty": 0.01, "avg_price": 100_000_000, "mark_price": 120_000_000, "value_krw": 1_200_000, "pnl_krw": 200_000},
        ]

    async def fetch_futures_assets(self, usdt_krw_rate: float | None = None) -> list[dict]:
        return [
            {"asset": "USDT-FUT", "kind": "cash", "qty": 500, "available": 500, "value_krw": 700_000, "pnl_krw": 0},
        ]

    async def fetch_futures_position_risk(self) -> list[dict]:
        return []


def _trader(tmp_path: Path) -> BinanceBlockTrader:
    return BinanceBlockTrader(
        config=BinanceBlockTraderConfig(
            db_path=str(tmp_path / "binance_blocks.db"),
            state_path=str(tmp_path / "binance_state.json"),
            enabled=True,
            execute_spot_orders=False,
            execute_futures_orders=False,
        ),
        binance=FakeBinance(),
        codex_runtime=SimpleNamespace(),
        memory_context_provider=lambda: {"persona": {"name": "쥬"}},
    )


def test_snapshot_initializes_db_and_returns_account(tmp_path: Path) -> None:
    trader = _trader(tmp_path)

    snapshot = pytest.run(asyncio=True)(trader.snapshot())

    assert snapshot["status"] == "ok"
    assert snapshot["execution"]["spot_mode"] == "paper"
    assert snapshot["execution"]["futures_mode"] == "paper"
    assert snapshot["account"]["spot_cash_usdt"] == pytest.approx(1000)
    assert snapshot["account"]["futures_cash_usdt"] == pytest.approx(500)
    assert snapshot["blocks"] == []
```

Use the repo's existing async test style instead of `pytest.run` if available. If not, replace the snapshot call with `asyncio.run(trader.snapshot())`.

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_binance_block_trader.py::test_snapshot_initializes_db_and_returns_account -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Create service module skeleton with real schema**

Create `src/tradecraft/services/binance_block_trader.py` with:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from tradecraft.services.binance import BinanceAdapter
from tradecraft.services.codex_runtime import CodexNativeRuntime

BINANCE_BLOCK_STATUSES = {
    "proposed",
    "entry_pending",
    "open",
    "exit_pending",
    "closed",
    "paused",
    "error",
}
BINANCE_ACTIVE_STATUSES = {"entry_pending", "open", "exit_pending"}
BINANCE_VENUES = {"spot", "futures"}
BINANCE_SIDES = {"long", "short"}


@dataclass
class BinanceBlockTraderConfig:
    db_path: str = ".runtime/binance_blocks.db"
    state_path: str = ".runtime/binance_block_trader.json"
    enabled: bool = False
    execute_spot_orders: bool = False
    execute_futures_orders: bool = False
    quote_interval_sec: int = 15
    rule_interval_sec: int = 15
    manager_interval_sec: int = 1800
    aggressive_limit_bps: float = 20.0
    max_manager_symbols: int = 12
    max_futures_leverage: int = 2
    min_liquidation_distance_pct: float = 12.0
    spot_universe: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
    futures_universe: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    manager_query: str = "쥬의 crypto branch로 Binance spot/futures 블록을 관리해줘"


class MemoryProvider(Protocol):
    def __call__(self) -> dict[str, Any]: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


class BinanceBlockTrader:
    def __init__(
        self,
        *,
        config: BinanceBlockTraderConfig,
        binance: BinanceAdapter,
        codex_runtime: CodexNativeRuntime,
        memory_context_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self.config = config
        self.binance = binance
        self.codex_runtime = codex_runtime
        self.memory_context_provider = memory_context_provider or (lambda: None)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        Path(self.config.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    block_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty_initial REAL NOT NULL DEFAULT 0,
                    qty_open REAL NOT NULL DEFAULT 0,
                    quote_budget_usdt REAL NOT NULL DEFAULT 0,
                    entry_price_usdt REAL NOT NULL DEFAULT 0,
                    target_price_usdt REAL NOT NULL DEFAULT 0,
                    stop_price_usdt REAL NOT NULL DEFAULT 0,
                    trailing_stop_pct REAL NOT NULL DEFAULT 0,
                    time_stop_at TEXT NOT NULL DEFAULT '',
                    leverage INTEGER NOT NULL DEFAULT 1,
                    margin_type TEXT NOT NULL DEFAULT '',
                    liquidation_price_usdt REAL NOT NULL DEFAULT 0,
                    thesis TEXT NOT NULL DEFAULT '',
                    llm_reason TEXT NOT NULL DEFAULT '',
                    risk_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    force_exit_requested INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT 'system',
                    manager_run_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS block_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    block_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS block_orders (
                    order_id TEXT PRIMARY KEY,
                    block_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    limit_price_usdt REAL NOT NULL,
                    reduce_only INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manager_runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    prompt_json TEXT NOT NULL DEFAULT '{}',
                    response_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quote_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    price_usdt REAL NOT NULL,
                    source TEXT NOT NULL DEFAULT 'binance',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kill_switch (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO kill_switch (id, enabled, reason, updated_at)
                VALUES (1, 0, '', datetime('now'));
                """
            )

    async def _account_snapshot(self) -> dict[str, Any]:
        spot_assets = await self.binance.fetch_spot_assets()
        futures_assets = await self.binance.fetch_futures_assets()
        futures_risk = await self.binance.fetch_futures_position_risk()
        return {
            "spot_assets": spot_assets,
            "futures_assets": futures_assets,
            "futures_position_risk": futures_risk,
            "spot_cash_usdt": sum(float(row.get("qty") or 0) for row in spot_assets if row.get("kind") == "cash" and row.get("asset") in {"USDT", "USDC"}),
            "futures_cash_usdt": sum(float(row.get("qty") or 0) for row in futures_assets if row.get("kind") == "cash"),
        }

    def _list_blocks(self, include_closed: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM blocks"
        params: list[Any] = []
        if not include_closed:
            query += " WHERE status NOT IN ('closed')"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) | {"metadata": _json_loads(row["metadata_json"], {})} for row in rows]

    async def snapshot(self) -> dict[str, Any]:
        account = await self._account_snapshot()
        return {
            "status": "ok" if self.config.enabled else "disabled",
            "enabled": self.config.enabled,
            "execution": {
                "spot_mode": "live" if self.config.execute_spot_orders else "paper",
                "futures_mode": "live" if self.config.execute_futures_orders else "paper",
            },
            "account": account,
            "blocks": self._list_blocks(include_closed=False),
            "updated_at": utc_now_iso(),
        }
```

- [ ] **Step 4: Fix test helper**

If the test used `pytest.run`, replace with:

```python
import asyncio

snapshot = asyncio.run(trader.snapshot())
```

- [ ] **Step 5: Verify**

```bash
pytest tests/test_binance_block_trader.py::test_snapshot_initializes_db_and_returns_account -q
ruff check src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: add binance block trader store"
```

---

## Task 4: Add Rule Executor For Spot And Futures Blocks

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing rule executor tests**

Add:

```python
def test_rule_executor_closes_spot_block_at_target(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.create_block_for_test(
        block_id="spot-btc-1",
        venue="spot",
        symbol="BTCUSDT",
        side="long",
        qty_open=0.01,
        entry_price_usdt=90000,
        target_price_usdt=100000,
        stop_price_usdt=85000,
        status="open",
    )

    result = asyncio.run(trader.executor_tick({"BTCUSDT": 101000.0}))

    assert result["actions"][0]["action"] == "close"
    assert result["actions"][0]["reason"] == "target_hit"
    block = trader.get_block("spot-btc-1")
    assert block["status"] == "exit_pending"


def test_rule_executor_closes_futures_short_at_stop(tmp_path: Path) -> None:
    trader = _trader(tmp_path)
    trader.create_block_for_test(
        block_id="fut-eth-short-1",
        venue="futures",
        symbol="ETHUSDT",
        side="short",
        qty_open=0.2,
        entry_price_usdt=3000,
        target_price_usdt=2700,
        stop_price_usdt=3150,
        leverage=2,
        margin_type="isolated",
        liquidation_price_usdt=4300,
        status="open",
    )

    result = asyncio.run(trader.executor_tick({"ETHUSDT": 3160.0}))

    assert result["actions"][0]["action"] == "close"
    assert result["actions"][0]["order_side"] == "BUY"
    assert result["actions"][0]["reduce_only"] is True
    assert result["actions"][0]["reason"] == "stop_hit"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_binance_block_trader.py::test_rule_executor_closes_spot_block_at_target tests/test_binance_block_trader.py::test_rule_executor_closes_futures_short_at_stop -q
```

Expected: FAIL with missing `create_block_for_test` or `executor_tick`.

- [ ] **Step 3: Add test helpers and rule executor**

Add methods:

```python
    def create_block_for_test(self, **row: Any) -> str:
        now = utc_now_iso()
        block_id = str(row["block_id"])
        payload = {
            "block_id": block_id,
            "venue": row.get("venue", "spot"),
            "symbol": row.get("symbol", "BTCUSDT"),
            "side": row.get("side", "long"),
            "qty_initial": float(row.get("qty_initial") or row.get("qty_open") or 0),
            "qty_open": float(row.get("qty_open") or 0),
            "quote_budget_usdt": float(row.get("quote_budget_usdt") or 0),
            "entry_price_usdt": float(row.get("entry_price_usdt") or 0),
            "target_price_usdt": float(row.get("target_price_usdt") or 0),
            "stop_price_usdt": float(row.get("stop_price_usdt") or 0),
            "trailing_stop_pct": float(row.get("trailing_stop_pct") or 0),
            "time_stop_at": str(row.get("time_stop_at") or ""),
            "leverage": int(row.get("leverage") or 1),
            "margin_type": str(row.get("margin_type") or ""),
            "liquidation_price_usdt": float(row.get("liquidation_price_usdt") or 0),
            "thesis": str(row.get("thesis") or ""),
            "llm_reason": str(row.get("llm_reason") or ""),
            "risk_note": str(row.get("risk_note") or ""),
            "status": str(row.get("status") or "open"),
            "created_by": "test",
            "manager_run_id": "",
            "metadata_json": _json_dumps(row.get("metadata") or {}),
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO blocks (
                    block_id, venue, symbol, side, qty_initial, qty_open, quote_budget_usdt,
                    entry_price_usdt, target_price_usdt, stop_price_usdt, trailing_stop_pct,
                    time_stop_at, leverage, margin_type, liquidation_price_usdt,
                    thesis, llm_reason, risk_note, status, created_by, manager_run_id,
                    metadata_json, created_at, updated_at
                ) VALUES (
                    :block_id, :venue, :symbol, :side, :qty_initial, :qty_open, :quote_budget_usdt,
                    :entry_price_usdt, :target_price_usdt, :stop_price_usdt, :trailing_stop_pct,
                    :time_stop_at, :leverage, :margin_type, :liquidation_price_usdt,
                    :thesis, :llm_reason, :risk_note, :status, :created_by, :manager_run_id,
                    :metadata_json, :created_at, :updated_at
                )
                """,
                payload,
            )
        return block_id

    def get_block(self, block_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM blocks WHERE block_id = ?", (block_id,)).fetchone()
        if row is None:
            raise KeyError(block_id)
        return dict(row) | {"metadata": _json_loads(row["metadata_json"], {})}

    async def executor_tick(self, quote_map: dict[str, float] | None = None) -> dict[str, Any]:
        quotes = quote_map or {}
        actions: list[dict[str, Any]] = []
        for block in self._list_blocks(include_closed=False):
            if block["status"] != "open":
                continue
            symbol = block["symbol"]
            price = float(quotes.get(symbol) or 0)
            if price <= 0:
                continue
            action = self._exit_action_for_block(block, price)
            if action is None:
                continue
            self._mark_exit_pending(block["block_id"], action)
            actions.append(action)
        return {"status": "ok", "actions": actions, "updated_at": utc_now_iso()}

    def _exit_action_for_block(self, block: dict[str, Any], price: float) -> dict[str, Any] | None:
        side = str(block["side"])
        target = float(block.get("target_price_usdt") or 0)
        stop = float(block.get("stop_price_usdt") or 0)
        if side == "long":
            if target > 0 and price >= target:
                reason = "target_hit"
            elif stop > 0 and price <= stop:
                reason = "stop_hit"
            else:
                return None
            order_side = "SELL"
        else:
            if target > 0 and price <= target:
                reason = "target_hit"
            elif stop > 0 and price >= stop:
                reason = "stop_hit"
            else:
                return None
            order_side = "BUY"
        return {
            "action": "close",
            "block_id": block["block_id"],
            "venue": block["venue"],
            "symbol": block["symbol"],
            "side": side,
            "order_side": order_side,
            "quantity": float(block.get("qty_open") or 0),
            "price": price,
            "reason": reason,
            "reduce_only": block["venue"] == "futures",
        }

    def _mark_exit_pending(self, block_id: str, action: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE blocks SET status = 'exit_pending', updated_at = ? WHERE block_id = ?",
                (utc_now_iso(), block_id),
            )
            conn.execute(
                "INSERT INTO block_events (block_id, event_type, message, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    block_id,
                    "rule_exit_signal",
                    str(action["reason"]),
                    _json_dumps(action),
                    utc_now_iso(),
                ),
            )
```

- [ ] **Step 4: Verify**

```bash
pytest tests/test_binance_block_trader.py -q
ruff check src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: add binance block rule executor"
```

---

## Task 5: Add Spark-Based Binance Manager

**Files:**
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Modify: `tests/test_binance_block_trader.py`

- [ ] **Step 1: Write failing manager test**

Add:

```python
class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete_json(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {
            "create_blocks": [
                {
                    "venue": "spot",
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "quote_budget_usdt": 100,
                    "target_price_usdt": 105000,
                    "stop_price_usdt": 97000,
                    "thesis": "BTC structure is constructive",
                    "confidence": 0.62,
                    "risk_note": "Position is small while regime is uncertain",
                },
                {
                    "venue": "futures",
                    "symbol": "ETHUSDT",
                    "side": "short",
                    "quote_budget_usdt": 50,
                    "target_price_usdt": 2800,
                    "stop_price_usdt": 3100,
                    "leverage": 2,
                    "margin_type": "isolated",
                    "thesis": "ETH relative weakness",
                    "confidence": 0.58,
                    "risk_note": "Short squeeze risk",
                },
            ],
            "update_blocks": [],
            "close_blocks": [],
            "pause_blocks": [],
        }


def test_manager_creates_spot_and_futures_blocks_with_spark_context(tmp_path: Path) -> None:
    trader = BinanceBlockTrader(
        config=BinanceBlockTraderConfig(
            db_path=str(tmp_path / "binance_blocks.db"),
            state_path=str(tmp_path / "binance_state.json"),
            enabled=True,
            execute_spot_orders=False,
            execute_futures_orders=False,
            max_futures_leverage=2,
        ),
        binance=FakeBinance(),
        codex_runtime=FakeLLM(),
        memory_context_provider=lambda: {"persona": {"name": "쥬"}, "active_policies": ["avoid overtrading"]},
    )

    result = asyncio.run(trader.run_manager_once())

    assert result["status"] == "ok"
    assert len(result["created_blocks"]) == 2
    blocks = trader.snapshot_sync()["blocks"]
    assert {row["venue"] for row in blocks} == {"spot", "futures"}
    assert trader.codex_runtime.calls[0]["model"] == "gpt-5.3-codex-spark"
    assert trader.codex_runtime.calls[0]["reasoning_effort"] == "high"
    assert "investment_memory" in trader.codex_runtime.calls[0]["input"]
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_binance_block_trader.py::test_manager_creates_spot_and_futures_blocks_with_spark_context -q
```

Expected: FAIL with missing `run_manager_once`.

- [ ] **Step 3: Add manager prompt and adoption**

Add `llm_model` and `llm_reasoning_effort` to `BinanceBlockTraderConfig`:

```python
    llm_model: str = "gpt-5.3-codex-spark"
    llm_reasoning_effort: str = "high"
```

Add:

```python
    async def run_manager_once(self) -> dict[str, Any]:
        run_id = f"binance_manager_{int(datetime.now(timezone.utc).timestamp())}"
        prompt = await self._build_manager_prompt()
        response = await self.codex_runtime.complete_json(
            {
                "model": self.config.llm_model,
                "reasoning_effort": self.config.llm_reasoning_effort,
                "input": prompt,
            }
        )
        created = self._apply_manager_response(run_id, response)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO manager_runs (run_id, mode, model, prompt_json, response_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    "manual",
                    self.config.llm_model,
                    _json_dumps(prompt),
                    _json_dumps(response),
                    "ok",
                    utc_now_iso(),
                ),
            )
        return {"status": "ok", "run_id": run_id, "created_blocks": created}

    async def _build_manager_prompt(self) -> dict[str, Any]:
        account = await self._account_snapshot()
        return {
            "agent": "Jue Binance Branch",
            "venue_scope": ["spot", "futures"],
            "manager_query": self.config.manager_query,
            "investment_memory": self.memory_context_provider() or {},
            "account": account,
            "existing_blocks": self._list_blocks(include_closed=False),
            "universe": {
                "spot": [item.strip().upper() for item in self.config.spot_universe.split(",") if item.strip()],
                "futures": [item.strip().upper() for item in self.config.futures_universe.split(",") if item.strip()],
            },
            "output_schema": {
                "create_blocks": "list of venue/symbol/side/quote_budget_usdt/target_price_usdt/stop_price_usdt/thesis/confidence/risk_note",
                "update_blocks": "list of block_id/target_price_usdt/stop_price_usdt/reason",
                "close_blocks": "list of block_id/reason",
                "pause_blocks": "list of block_id/reason",
            },
            "safety": {
                "spot_only_long": True,
                "futures_margin_type": "isolated",
                "max_futures_leverage": self.config.max_futures_leverage,
                "manager_can_request_orders_but_gate_executes": True,
            },
        }

    def _apply_manager_response(self, run_id: str, response: dict[str, Any]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for row in response.get("create_blocks") or []:
            if not isinstance(row, dict):
                continue
            venue = str(row.get("venue") or "").lower().strip()
            symbol = str(row.get("symbol") or "").upper().strip()
            side = str(row.get("side") or "long").lower().strip()
            if venue not in BINANCE_VENUES or not symbol:
                continue
            if venue == "spot" and side != "long":
                continue
            leverage = int(float(row.get("leverage") or 1))
            if venue == "futures" and leverage > self.config.max_futures_leverage:
                continue
            margin_type = str(row.get("margin_type") or ("isolated" if venue == "futures" else "")).lower()
            if venue == "futures" and margin_type != "isolated":
                continue
            block_id = f"{venue}_{symbol}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
            self.create_block_for_test(
                block_id=block_id,
                venue=venue,
                symbol=symbol,
                side=side,
                quote_budget_usdt=float(row.get("quote_budget_usdt") or 0),
                target_price_usdt=float(row.get("target_price_usdt") or 0),
                stop_price_usdt=float(row.get("stop_price_usdt") or 0),
                leverage=leverage,
                margin_type=margin_type,
                thesis=row.get("thesis") or "",
                llm_reason=row.get("thesis") or "",
                risk_note=row.get("risk_note") or "",
                status="proposed",
                metadata={"confidence": float(row.get("confidence") or 0)},
            )
            created.append({"block_id": block_id, "venue": venue, "symbol": symbol})
        return created
```

- [ ] **Step 4: Add synchronous snapshot helper for tests**

Add:

```python
    def snapshot_sync(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.config.enabled else "disabled",
            "enabled": self.config.enabled,
            "blocks": self._list_blocks(include_closed=False),
            "updated_at": utc_now_iso(),
        }
```

- [ ] **Step 5: Verify**

```bash
pytest tests/test_binance_block_trader.py -q
ruff check src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/services/binance_block_trader.py tests/test_binance_block_trader.py
git commit -m "feat: add spark binance block manager"
```

---

## Task 6: Add 24-Hour Runner And Process Status

**Files:**
- Create: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Modify: `src/tradecraft/runtime/process_status.py`
- Modify: `pyproject.toml`
- Create: `tests/test_binance_block_trader_runner.py`

- [ ] **Step 1: Write failing runner test**

Create:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from tradecraft.runtime.binance_block_trader_runner import run_binance_block_trader_loop


class FakeTrader:
    def __init__(self) -> None:
        self.executor_ticks = 0
        self.manager_runs = 0

    async def executor_tick(self) -> dict:
        self.executor_ticks += 1
        return {"status": "ok", "actions": []}

    async def run_manager_once(self) -> dict:
        self.manager_runs += 1
        return {"status": "ok", "created_blocks": []}


class Settings:
    binance_block_trader_state_path = ""
    binance_block_trader_rule_interval_sec = 1
    binance_block_trader_manager_interval_sec = 1
    binance_block_trader_once = True


def test_runner_writes_state_once(tmp_path: Path) -> None:
    settings = Settings()
    settings.binance_block_trader_state_path = str(tmp_path / "state.json")
    trader = FakeTrader()

    asyncio.run(run_binance_block_trader_loop(settings=settings, trader=trader, sleep=lambda _: asyncio.sleep(0)))

    assert trader.executor_ticks == 1
    assert trader.manager_runs == 1
    assert Path(settings.binance_block_trader_state_path).exists()
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_binance_block_trader_runner.py -q
```

Expected: FAIL because runner module does not exist.

- [ ] **Step 3: Add runner**

Create `src/tradecraft/runtime/binance_block_trader_runner.py`:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import clear_current_runner_pid, write_current_runner_pid
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.binance import BinanceAdapter, BinanceConfig
from tradecraft.services.binance_block_trader import BinanceBlockTrader, BinanceBlockTraderConfig
from tradecraft.services.investment_memory import InvestmentMemoryConfig, InvestmentMemoryService
from tradecraft.services.codex_runtime import CodexNativeRuntime, CodexNativeConfig

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _build_trader(settings: AppSettings) -> BinanceBlockTrader:
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            command=settings.codex_runtime_command,
            args=settings.codex_runtime_args,
            url=settings.codex_runtime_url,
            token=settings.codex_runtime_token,
            timeout_ms=settings.codex_runtime_timeout_ms,
            model=settings.binance_block_trader_llm_model,
            reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="binance_block_manager",
        )
    )
    memory = InvestmentMemoryService(
        config=InvestmentMemoryConfig(
            root_path=settings.investment_memory_root_path,
            db_path=settings.investment_memory_db_path,
            strategy_md_path=settings.research_strategy_md_path,
            policy_mode=settings.investment_memory_policy_mode,
            persona_tone=settings.investment_memory_persona_tone,
            telegram_enabled=False,
            context_max_chars=settings.investment_memory_context_max_chars,
        ),
        codex_runtime=bridge,
    )
    return BinanceBlockTrader(
        config=BinanceBlockTraderConfig(
            db_path=settings.binance_block_trader_db_path,
            state_path=settings.binance_block_trader_state_path,
            enabled=settings.binance_block_trader_enabled,
            execute_spot_orders=settings.binance_block_trader_execute_spot_orders,
            execute_futures_orders=settings.binance_block_trader_execute_futures_orders,
            quote_interval_sec=settings.binance_block_trader_quote_interval_sec,
            rule_interval_sec=settings.binance_block_trader_rule_interval_sec,
            manager_interval_sec=settings.binance_block_trader_manager_interval_sec,
            aggressive_limit_bps=settings.binance_block_trader_aggressive_limit_bps,
            max_manager_symbols=settings.binance_block_trader_max_manager_symbols,
            max_futures_leverage=settings.binance_block_trader_max_futures_leverage,
            min_liquidation_distance_pct=settings.binance_block_trader_min_liquidation_distance_pct,
            spot_universe=settings.binance_block_trader_spot_universe,
            futures_universe=settings.binance_block_trader_futures_universe,
            llm_model=settings.binance_block_trader_llm_model,
            llm_reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
        ),
        binance=BinanceAdapter(
            BinanceConfig(
                spot_api_key=settings.binance_spot_api_key,
                spot_api_secret=settings.binance_spot_api_secret,
                spot_base_url=settings.binance_spot_base_url,
                futures_api_key=settings.binance_futures_api_key,
                futures_api_secret=settings.binance_futures_api_secret,
                futures_base_url=settings.binance_futures_base_url,
                usdt_krw_rate=settings.binance_usdt_krw,
            )
        ),
        codex_runtime=bridge,
        memory_context_provider=memory.context_pack,
    )


async def run_binance_block_trader_loop(
    *,
    settings: AppSettings,
    trader: BinanceBlockTrader | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_trader = trader or _build_trader(settings)
    state = RuntimeStateStore(settings.binance_block_trader_state_path)
    manager_interval = max(int(settings.binance_block_trader_manager_interval_sec), 60)
    rule_interval = max(int(settings.binance_block_trader_rule_interval_sec), 5)
    last_manager_at = 0.0

    while True:
        now = datetime.now(timezone.utc).timestamp()
        tick = await resolved_trader.executor_tick()
        manager_result: dict[str, Any] | None = None
        if now - last_manager_at >= manager_interval or settings.binance_block_trader_once:
            manager_result = await resolved_trader.run_manager_once()
            last_manager_at = now
        state.write(
            {
                "status": "ok",
                "runner": "binance_block_trader",
                "tick": tick,
                "manager": manager_result,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if settings.binance_block_trader_once:
            return
        await sleep(rule_interval)


def main() -> None:
    settings = AppSettings()
    if not settings.binance_block_trader_enabled:
        logger.info("Binance block trader disabled")
        return
    write_current_runner_pid("binance_block_trader")
    try:
        asyncio.run(run_binance_block_trader_loop(settings=settings))
    finally:
        clear_current_runner_pid("binance_block_trader")
```

- [ ] **Step 4: Add process status key**

In `src/tradecraft/runtime/process_status.py`, add:

```python
"binance_block_trader": "tradecraft-binance-block-trader.pid",
```

Add regex:

```python
"binance_block_trader": (
    r"tradecraft-binance-block-trader|tradecraft\.runtime\.binance_block_trader_runner|"
    r"binance_block_trader_runner\.py"
),
```

Add label:

```python
"binance_block_trader": "Binance block trader runner",
```

- [ ] **Step 5: Add entry point**

In `pyproject.toml` entry points:

```toml
tradecraft-binance-block-trader = "tradecraft.runtime.binance_block_trader_runner:main"
```

- [ ] **Step 6: Verify**

```bash
pytest tests/test_binance_block_trader_runner.py tests/test_process_status.py -q
ruff check src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/runtime/process_status.py tests/test_binance_block_trader_runner.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/runtime/process_status.py pyproject.toml tests/test_binance_block_trader_runner.py
git commit -m "feat: add binance block trader runner"
```

---

## Task 7: Add Admin-Protected API Routes

**Files:**
- Modify: `src/tradecraft/main.py`
- Create: `tests/test_binance_trader_api.py`

- [ ] **Step 1: Write failing API tests**

Create:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from tradecraft import main


def _admin_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    monkeypatch.setattr(main.settings, "admin_tokens", "")
    return {"Authorization": "Bearer test-admin"}


def test_binance_blocks_status_requires_admin(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "admin_token", "test-admin")
    with TestClient(main.app) as client:
        response = client.get("/api/binance/blocks/status")
    assert response.status_code == 401


def test_binance_blocks_status_returns_snapshot(monkeypatch) -> None:
    async def fake_snapshot() -> dict:
        return {"status": "ok", "blocks": [], "execution": {"spot_mode": "paper", "futures_mode": "paper"}}

    monkeypatch.setattr(main.binance_block_trader, "snapshot", fake_snapshot)
    with TestClient(main.app) as client:
        response = client.get("/api/binance/blocks/status", headers=_admin_headers(monkeypatch))

    assert response.status_code == 200
    assert response.json()["execution"]["spot_mode"] == "paper"


def test_binance_manager_run_once(monkeypatch) -> None:
    async def fake_run() -> dict:
        return {"status": "ok", "created_blocks": [{"block_id": "b1"}]}

    monkeypatch.setattr(main.binance_block_trader, "run_manager_once", fake_run)
    with TestClient(main.app) as client:
        response = client.post("/api/binance/blocks/manager/run-once", headers=_admin_headers(monkeypatch))

    assert response.status_code == 200
    assert response.json()["created_blocks"][0]["block_id"] == "b1"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_binance_trader_api.py -q
```

Expected: FAIL because `main.binance_block_trader` or routes do not exist.

- [ ] **Step 3: Instantiate service in `main.py`**

Add imports:

```python
from tradecraft.services.binance_block_trader import BinanceBlockTrader, BinanceBlockTraderConfig
```

After `binance = BinanceAdapter(...)`, add a Binance manager bridge and service:

```python
binance_manager_codex_runtime = CodexNativeRuntime(
    CodexNativeConfig(
        command=settings.codex_runtime_command,
        args=settings.codex_runtime_args,
        url=settings.codex_runtime_url,
        token=settings.codex_runtime_token,
        timeout_ms=settings.codex_runtime_timeout_ms,
        model=settings.binance_block_trader_llm_model,
        reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
        usage_enabled=settings.llm_usage_enabled,
        usage_db_path=settings.llm_usage_db_path,
        usage_component="binance_block_manager",
    )
)
binance_block_trader = BinanceBlockTrader(
    config=BinanceBlockTraderConfig(
        db_path=settings.binance_block_trader_db_path,
        state_path=settings.binance_block_trader_state_path,
        enabled=settings.binance_block_trader_enabled,
        execute_spot_orders=settings.binance_block_trader_execute_spot_orders,
        execute_futures_orders=settings.binance_block_trader_execute_futures_orders,
        quote_interval_sec=settings.binance_block_trader_quote_interval_sec,
        rule_interval_sec=settings.binance_block_trader_rule_interval_sec,
        manager_interval_sec=settings.binance_block_trader_manager_interval_sec,
        aggressive_limit_bps=settings.binance_block_trader_aggressive_limit_bps,
        max_manager_symbols=settings.binance_block_trader_max_manager_symbols,
        max_futures_leverage=settings.binance_block_trader_max_futures_leverage,
        min_liquidation_distance_pct=settings.binance_block_trader_min_liquidation_distance_pct,
        spot_universe=settings.binance_block_trader_spot_universe,
        futures_universe=settings.binance_block_trader_futures_universe,
        llm_model=settings.binance_block_trader_llm_model,
        llm_reasoning_effort=settings.binance_block_trader_llm_reasoning_effort,
    ),
    binance=binance,
    codex_runtime=binance_manager_codex_runtime,
    memory_context_provider=investment_memory_service.context_pack,
)
```

- [ ] **Step 4: Add routes**

Add after KIS block routes or near market routes:

```python
@app.get("/api/binance/blocks/status")
async def binance_blocks_status(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return await binance_block_trader.snapshot()


@app.get("/api/binance/blocks")
async def binance_blocks(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return await binance_block_trader.snapshot()


@app.post("/api/binance/blocks/manager/run-once")
async def binance_blocks_manager_run_once(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return await binance_block_trader.run_manager_once()


@app.post("/api/binance/blocks/executor/tick")
async def binance_blocks_executor_tick(_: None = Depends(require_admin_auth)) -> dict[str, Any]:
    return await binance_block_trader.executor_tick()
```

- [ ] **Step 5: Verify**

```bash
pytest tests/test_binance_trader_api.py tests/test_admin_auth.py -q
ruff check src/tradecraft/main.py tests/test_binance_trader_api.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tradecraft/main.py tests/test_binance_trader_api.py
git commit -m "feat: expose binance block trader api"
```

---

## Task 8: Add Dedicated Binance Trading UI Tab

**Files:**
- Modify: `src/tradecraft/web/static/index.html`
- Modify: `src/tradecraft/web/static/app.js`
- Modify: `src/tradecraft/web/static/style.css`

- [ ] **Step 1: Add UI state and loader**

In `state`, add:

```javascript
  binanceTrader: {
    status: null,
    loading: false,
    running: false,
    error: "",
  },
```

Add:

```javascript
async function loadBinanceBlocks(runManager = false) {
  state.binanceTrader.loading = !runManager;
  state.binanceTrader.running = runManager;
  state.binanceTrader.error = "";
  renderHelperAgent();
  try {
    state.binanceTrader.status = await getJSON(
      runManager ? "/binance/blocks/manager/run-once" : "/binance/blocks/status",
      { method: runManager ? "POST" : "GET" }
    );
    if (runManager) {
      state.binanceTrader.status = await getJSON("/binance/blocks/status");
    }
  } catch (error) {
    state.binanceTrader.error = getErrorMessage(error);
  } finally {
    state.binanceTrader.loading = false;
    state.binanceTrader.running = false;
    renderHelperAgent();
  }
}
```

- [ ] **Step 2: Add helper tab button**

In `index.html`, add to helper tabs:

```html
<button type="button" class="tab-button" data-helper-tab="binance_trader">바이낸스</button>
```

Add to left nav if desired:

```html
<button class="nav-item" type="button" data-nav-helper-tab="binance_trader">
  <span>Binance</span>
  <strong>크립토 블록</strong>
</button>
```

- [ ] **Step 3: Add renderer**

Add in `app.js`:

```javascript
function renderBinanceTraderTab() {
  const payload = state.binanceTrader.status;
  const error = state.binanceTrader.error;
  if (error) return `<div class="notice">바이낸스 상태 조회 실패: ${escapeHTML(error)}</div>`;
  if (!payload) return `<div class="notice">바이낸스 블록 상태를 불러오는 중입니다.</div>`;

  const execution = payload.execution || {};
  const account = payload.account || {};
  const blocks = Array.isArray(payload.blocks) ? payload.blocks : [];
  const cards = blocks.length
    ? blocks.map((block) => `
        <article class="binance-block-card ${escapeHTML(block.venue || "spot")}">
          <div class="block-card-head">
            <div>
              <span class="section-kicker">${escapeHTML(block.venue || "-")} · ${escapeHTML(block.side || "-")}</span>
              <h4>${escapeHTML(block.symbol || "-")}</h4>
            </div>
            <span class="helper-runtime-chip ${String(block.status) === "open" ? "ok" : "warn"}">${escapeHTML(block.status || "-")}</span>
          </div>
          <div class="block-price-grid">
            <div><span>진입</span><strong>${escapeHTML(fmtNum(block.entry_price_usdt, 4))}</strong></div>
            <div><span>목표</span><strong>${escapeHTML(fmtNum(block.target_price_usdt, 4))}</strong></div>
            <div><span>손절</span><strong>${escapeHTML(fmtNum(block.stop_price_usdt, 4))}</strong></div>
            <div><span>수량</span><strong>${escapeHTML(fmtNum(block.qty_open, 8))}</strong></div>
          </div>
          <p class="helper-text">${escapeHTML(block.thesis || block.llm_reason || "-")}</p>
        </article>
      `).join("")
    : `<div class="notice">활성 바이낸스 블록이 없습니다.</div>`;

  return `
    <div class="binance-trader-shell">
      <section class="block-trader-hero">
        <div>
          <span class="section-kicker">24H Crypto Branch</span>
          <h3>바이낸스 쥬 브랜치</h3>
          <p>현물과 선물을 별도 게이트로 관리하고, GPT-5.3-Codex-Spark가 24시간 경량 판단을 수행합니다.</p>
        </div>
        <div class="strategy-intel-actions">
          <button class="btn ghost" type="button" data-binance-action="refresh">새로고침</button>
          <button class="btn warm" type="button" data-binance-action="manager" ${state.binanceTrader.running ? "disabled" : ""}>
            ${state.binanceTrader.running ? "쥬 판단 중..." : "쥬 판단 1회"}
          </button>
        </div>
      </section>
      <div class="block-trader-kpis">
        <article class="mini-card"><p>현물 모드</p><h4>${escapeHTML(execution.spot_mode || "-")}</h4></article>
        <article class="mini-card"><p>선물 모드</p><h4>${escapeHTML(execution.futures_mode || "-")}</h4></article>
        <article class="mini-card"><p>현물 USDT</p><h4>${escapeHTML(fmtNum(account.spot_cash_usdt, 2))}</h4></article>
        <article class="mini-card"><p>선물 USDT</p><h4>${escapeHTML(fmtNum(account.futures_cash_usdt, 2))}</h4></article>
      </div>
      <div class="binance-block-grid">${cards}</div>
    </div>
  `;
}
```

- [ ] **Step 4: Wire tab**

In valid helper tabs, add `"binance_trader"`.

In `ensureHelperTabData`, add:

```javascript
  if (tab === "binance_trader" && !state.binanceTrader.status && !state.binanceTrader.loading) {
    loadBinanceBlocks(false);
  }
```

In `renderHelperAgent`, add:

```javascript
  } else if (state.activeHelperTab === "binance_trader") {
    contentHtml = renderBinanceTraderTab();
    updatedAt = pickUpdatedAt(state.binanceTrader.status) || updatedAt;
```

In `titleMap`, add:

```javascript
binance_trader: "바이낸스",
```

In click handler, add:

```javascript
    const binanceAction = target ? target.closest("[data-binance-action]") : null;
    if (binanceAction) {
      const action = String(binanceAction.dataset.binanceAction || "refresh");
      loadBinanceBlocks(action === "manager");
      return;
    }
```

- [ ] **Step 5: Add CSS**

Add:

```css
.binance-trader-shell,
.binance-block-grid {
  display: grid;
  gap: 12px;
}

.binance-block-grid {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.binance-block-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 13px;
  background: var(--surface);
}

.binance-block-card.futures {
  border-color: var(--status-warn-line);
  background: color-mix(in srgb, var(--surface) 86%, var(--status-warn-soft) 14%);
}

.binance-block-card.spot {
  border-color: var(--accent-line);
  background: color-mix(in srgb, var(--surface) 88%, var(--accent-soft) 12%);
}
```

- [ ] **Step 6: Verify JS**

```bash
node --check src/tradecraft/web/static/app.js
git diff --check -- src/tradecraft/web/static/index.html src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css
```

Expected: no output and exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/tradecraft/web/static/index.html src/tradecraft/web/static/app.js src/tradecraft/web/static/style.css
git commit -m "feat: add binance trading ui tab"
```

---

## Task 9: Add Readiness And Smoke Coverage

**Files:**
- Modify: `src/tradecraft/main.py`
- Modify: `tests/test_api_smoke.py`

- [ ] **Step 1: Write failing readiness test**

Add to `tests/test_api_smoke.py`:

```python
def test_ops_readiness_includes_binance_block_trader(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "admin_tokens", "")
    monkeypatch.setattr(settings, "binance_block_trader_enabled", True)
    monkeypatch.setattr(settings, "binance_block_trader_execute_spot_orders", False)
    monkeypatch.setattr(settings, "binance_block_trader_execute_futures_orders", False)

    with TestClient(app) as client:
        response = client.get("/api/ops/readiness", headers={"Authorization": "Bearer test-admin"})

    assert response.status_code == 200
    payload = response.json()
    assert "binance_block_trader" in payload
    assert payload["binance_block_trader"]["execution"]["spot_mode"] == "paper"
    assert payload["binance_block_trader"]["execution"]["futures_mode"] == "paper"
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_api_smoke.py::test_ops_readiness_includes_binance_block_trader -q
```

Expected: FAIL because readiness payload lacks `binance_block_trader`.

- [ ] **Step 3: Add readiness payload**

In `_build_ops_readiness()` or its helper in `main.py`, include:

```python
"binance_block_trader": {
    "enabled": bool(settings.binance_block_trader_enabled),
    "execution": {
        "spot_mode": "live" if settings.binance_block_trader_execute_spot_orders else "paper",
        "futures_mode": "live" if settings.binance_block_trader_execute_futures_orders else "paper",
    },
    "runner": processes.get("binance_block_trader", {}),
    "model": settings.binance_block_trader_llm_model,
    "reasoning_effort": settings.binance_block_trader_llm_reasoning_effort,
}
```

In `_build_core_runner_processes()`, add `binance_block_trader`.

- [ ] **Step 4: Verify**

```bash
pytest tests/test_api_smoke.py::test_ops_readiness_includes_binance_block_trader tests/test_api_smoke.py::test_health_and_dashboard -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tradecraft/main.py tests/test_api_smoke.py
git commit -m "feat: add binance readiness status"
```

---

## Task 10: End-To-End Verification And Local Rollout

**Files:**
- No source edits unless verification exposes a defect.

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_binance_adapter.py tests/test_binance_block_trader.py tests/test_binance_block_trader_runner.py tests/test_binance_trader_api.py -q
```

Expected: all pass.

- [ ] **Step 2: Run related smoke tests**

```bash
pytest tests/test_config.py tests/test_admin_auth.py tests/test_api_smoke.py -q
```

Expected: all pass.

- [ ] **Step 3: Static frontend check**

```bash
node --check src/tradecraft/web/static/app.js
git diff --check
```

Expected: no output from `git diff --check`, JS check exits 0.

- [ ] **Step 4: Start paper-mode Binance runner**

Set local `.env` values:

```bash
TRADECRAFT_BINANCE_BLOCK_TRADER_ENABLED=true
TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_SPOT_ORDERS=false
TRADECRAFT_BINANCE_BLOCK_TRADER_EXECUTE_FUTURES_ORDERS=false
TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_MODEL=gpt-5.3-codex-spark
TRADECRAFT_BINANCE_BLOCK_TRADER_LLM_REASONING_EFFORT=high
```

Run in tmux:

```bash
tmux new-session -d -s tradecraft-binance-block-trader 'cd /Users/juhwan/hermes_v2 && source .venv/bin/activate && tradecraft-binance-block-trader'
```

Expected: session remains alive.

- [ ] **Step 5: Restart control app**

```bash
tmux kill-session -t tradecraft-control 2>/dev/null || true
tmux new-session -d -s tradecraft-control 'cd /Users/juhwan/hermes_v2 && source .venv/bin/activate && TRADECRAFT_PORT=18080 tradecraft-control'
sleep 2
curl -sS http://127.0.0.1:18080/api/health
```

Expected:

```json
{"status":"ok","service":"tradecraft-control","ops_endpoint":"/api/ops/readiness","ops_auth_required":true}
```

- [ ] **Step 6: Verify API manually**

```bash
TOKEN="$(grep '^TRADECRAFT_ADMIN_TOKEN=' .env | cut -d= -f2- | tr -d '\"')"
curl -sS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18080/api/binance/blocks/status
```

Expected: JSON with `status`, `execution`, `account`, `blocks`.

- [ ] **Step 7: Browser smoke**

Open `http://127.0.0.1:18080/`, enter admin token if prompted, click `바이낸스`.

Expected:
- Binance tab renders without horizontal overflow.
- Spot/futures execution mode is visible.
- Cash values load.
- `쥬 판단 1회` creates paper/proposed blocks when Spark returns valid candidates.

- [ ] **Step 8: Commit verification fixes if any**

If verification required code changes:

```bash
git add <changed files>
git commit -m "fix: stabilize binance branch verification"
```

If no verification fixes were needed, do not create an empty commit.

---

## Self-Review

- Spec coverage: The plan covers spot and futures, a separate UI tab, Spark model routing, 24-hour runner, shared Jue memory context, rule-based block execution, admin APIs, readiness, settings, and tests.
- Placeholder scan: The plan contains no `TBD`, no empty tasks, and no unspecific “handle edge cases” instructions.
- Type consistency: `BinanceBlockTraderConfig`, `BinanceBlockTrader`, `run_binance_block_trader_loop`, route paths, setting names, and tab id `binance_trader` are consistent across tasks.
- Scope check: Futures live execution is included behind an explicit flag and isolated-margin/leverage gates. Advanced futures features such as funding-aware sizing, hedge mode, cross margin, and liquidation auto-rescue are excluded from v1 to keep this implementation testable.
