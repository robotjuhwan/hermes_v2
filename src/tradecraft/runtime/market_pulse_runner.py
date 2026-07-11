from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.process_status import write_current_runner_pid
from tradecraft.runtime.state_store import RuntimeStateStore
from tradecraft.services.intelligence import build_report_intelligence_stack
from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    codex_native_thread_config_kwargs,
)
from tradecraft.services.llm_model_policy import llm_model_config_kwargs
from tradecraft.services.market_judgment import build_market_clock
from tradecraft.services.market_pulse import MarketPulseConfig, MarketPulseService
from tradecraft.services.kis_horizon import ACTIVE_BLOCK_STATUSES
from tradecraft.services.strategy_intelligence import (
    StrategyIntelligenceConfig,
    StrategyIntelligenceEngine,
)
from tradecraft.services.symbol_fundamentals import (
    SymbolFundamentalsConfig,
    SymbolFundamentalsService,
)

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[None]]


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default


def _load_kis_block_context(db_path: str | Path, *, limit: int = 100) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return {
            "status": "missing",
            "blocks": [],
            "quotes": {},
            "block_count": 0,
            "quote_count": 0,
        }
    statuses = sorted(ACTIVE_BLOCK_STATUSES)
    placeholders = ",".join("?" for _ in statuses)
    query = f"""
        SELECT block_id, symbol, name, status, qty_initial, qty_open, metadata_json
        FROM blocks
        WHERE status IN ({placeholders})
          AND (qty_open > 0 OR status = 'entry_pending')
        ORDER BY created_at DESC, block_id DESC
        LIMIT ?
    """
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, [*statuses, max(int(limit), 1)]).fetchall()
        blocks = []
        for row in rows:
            metadata = _json_loads(row["metadata_json"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            blocks.append(
                {
                    "block_id": str(row["block_id"] or ""),
                    "symbol": str(row["symbol"] or ""),
                    "name": str(row["name"] or ""),
                    "status": str(row["status"] or ""),
                    "qty_initial": int(row["qty_initial"] or 0),
                    "qty_open": int(row["qty_open"] or 0),
                    "sector": str(metadata.get("sector") or ""),
                    "market": str(metadata.get("market") or ""),
                }
            )
        symbols = sorted({str(row.get("symbol") or "") for row in blocks if row.get("symbol")})
        quotes: dict[str, dict[str, Any]] = {}
        if symbols:
            symbol_placeholders = ",".join("?" for _ in symbols)
            quote_rows = conn.execute(
                f"""
                SELECT q.symbol, q.name, q.price, q.source, q.fetched_at, q.status,
                       q.error_message, q.raw_json
                FROM quote_snapshots q
                INNER JOIN (
                    SELECT symbol, MAX(id) AS id
                    FROM quote_snapshots
                    WHERE symbol IN ({symbol_placeholders})
                      AND status != 'error'
                    GROUP BY symbol
                ) latest ON latest.id = q.id
                """,
                symbols,
            ).fetchall()
            for row in quote_rows:
                quotes[str(row["symbol"] or "")] = {
                    "symbol": str(row["symbol"] or ""),
                    "name": str(row["name"] or ""),
                    "price": row["price"],
                    "source": str(row["source"] or ""),
                    "fetched_at": str(row["fetched_at"] or ""),
                    "status": str(row["status"] or ""),
                    "error_message": str(row["error_message"] or ""),
                    "raw": _json_loads(row["raw_json"], {}),
                }
    return {
        "status": "ok",
        "blocks": blocks,
        "quotes": quotes,
        "block_count": len(blocks),
        "quote_count": len(quotes),
    }


def _build_market_pulse(settings: AppSettings) -> MarketPulseService:
    stack = build_report_intelligence_stack(settings)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode=settings.codex_runtime_mode,
            sdk_codex_bin=settings.codex_runtime_sdk_codex_bin,
            timeout_ms=settings.codex_runtime_timeout_ms,
            **llm_model_config_kwargs(settings, component="strategy_intelligence"),
            usage_enabled=settings.llm_usage_enabled,
            usage_db_path=settings.llm_usage_db_path,
            usage_component="strategy_intelligence",
            **codex_native_thread_config_kwargs(settings),
        )
    )
    fundamentals = SymbolFundamentalsService(
        SymbolFundamentalsConfig(
            db_path=settings.valuation_db_path,
            timeout_sec=settings.valuation_timeout_sec,
            min_refresh_hours=settings.valuation_min_refresh_hours,
            max_symbols_per_collect=settings.valuation_max_symbols_per_collect,
        )
    )
    strategy_engine = StrategyIntelligenceEngine(
        repository=stack.repository,
        rag_store=stack.rag_store,
        codex_runtime=bridge,
        fundamentals_repository=fundamentals,
        config=StrategyIntelligenceConfig(
            insight_db_path=settings.strategy_insight_db_path,
            model_timeout_ms=settings.codex_runtime_timeout_ms,
        ),
    )
    return MarketPulseService(
        config=MarketPulseConfig(
            db_path=settings.market_pulse_db_path,
            enabled=settings.market_pulse_enabled,
            timeout_sec=settings.market_pulse_timeout_sec,
            index_codes=settings.market_pulse_index_codes,
            sector_signal_limit=settings.market_pulse_sector_signal_limit,
            investor_flow_enabled=settings.market_pulse_investor_flow_enabled,
            investor_flow_markets=settings.market_pulse_investor_flow_markets,
            program_trading_enabled=settings.market_pulse_program_trading_enabled,
            program_trading_markets=settings.market_pulse_program_trading_markets,
            fx_enabled=settings.market_pulse_fx_enabled,
        ),
        strategy_signal_provider=strategy_engine,
    )


def _market_pulse_sleep_interval(settings: AppSettings, clock: dict[str, Any]) -> int:
    regular_interval = max(int(settings.market_pulse_interval_sec), 30)
    closed_interval = max(
        int(getattr(settings, "market_pulse_closed_interval_sec", regular_interval)),
        regular_interval,
    )
    session = str(clock.get("session") or "").strip().lower()
    is_market_open = bool(clock.get("is_market_open"))
    if is_market_open or session in {"regular", "closing_watch", "pre_open"}:
        return regular_interval
    return closed_interval


async def run_market_pulse_loop(
    *,
    settings: AppSettings | None = None,
    service: MarketPulseService | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    resolved_settings = settings or AppSettings()
    state_store = RuntimeStateStore(resolved_settings.market_pulse_state_path)
    resolved_service = service or _build_market_pulse(resolved_settings)
    cycle = 0

    while True:
        cycle += 1
        clock = build_market_clock()
        interval = _market_pulse_sleep_interval(resolved_settings, clock)
        block_context: dict[str, Any] = {
            "status": "not_loaded",
            "blocks": [],
            "quotes": {},
            "block_count": 0,
            "quote_count": 0,
        }
        try:
            block_context = _load_kis_block_context(
                resolved_settings.kis_block_trader_db_path
            )
            result = await resolved_service.collect(
                clock=clock,
                blocks=list(block_context.get("blocks") or []),
                quotes=dict(block_context.get("quotes") or {}),
            )
            status = str(result.get("status") or "ok")
            try:
                resolved_service.repository.prune_history(
                    retention_days=int(resolved_settings.market_pulse_retention_days),
                    archive_retention_days=int(
                        resolved_settings.market_pulse_archive_retention_days
                    ),
                )
            except Exception as exc:
                logger.warning("market pulse retention cleanup failed: %s", exc)
        except Exception as exc:
            logger.exception("market pulse cycle failed")
            status = "error"
            result = {"status": "error", "error_message": str(exc)}

        snapshot = {
            "service": "tradecraft-market-pulse",
            "status": status,
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "interval_sec": interval,
            "regular_interval_sec": max(
                int(resolved_settings.market_pulse_interval_sec),
                30,
            ),
            "closed_interval_sec": max(
                int(resolved_settings.market_pulse_closed_interval_sec),
                max(int(resolved_settings.market_pulse_interval_sec), 30),
            ),
            "kis_block_context": {
                "status": block_context.get("status"),
                "block_count": block_context.get("block_count"),
                "quote_count": block_context.get("quote_count"),
            },
            "result": result,
        }
        state_store.write_snapshot(snapshot)
        logger.info(
            "market pulse cycle=%s status=%s regime=%s score=%s kis_blocks=%s",
            cycle,
            status,
            result.get("regime") if isinstance(result, dict) else "",
            result.get("score") if isinstance(result, dict) else "",
            block_context.get("block_count"),
        )
        if resolved_settings.market_pulse_once:
            return
        await sleep(float(interval))


def run() -> None:
    write_current_runner_pid("market_pulse")
    settings = AppSettings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not settings.market_pulse_enabled:
        logger.info("market pulse disabled: TRADECRAFT_MARKET_PULSE_ENABLED=false")
        return
    try:
        asyncio.run(run_market_pulse_loop(settings=settings))
    except KeyboardInterrupt:
        logger.info("market pulse runner interrupted; stopping")


if __name__ == "__main__":
    run()
