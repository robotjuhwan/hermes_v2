from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from tradecraft.config import AppSettings
from tradecraft.runtime.crypto_market_research_runner import parse_crypto_universe
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)

logger = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[Any]]


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    return getattr(settings, name, default)


def build_crypto_pattern_lab_service(settings: AppSettings) -> Any:
    from tradecraft.services.crypto_pattern_lab import (
        CryptoPatternLabConfig,
        CryptoPatternLabService,
        HermesKlineReader,
    )

    return CryptoPatternLabService(
        config=CryptoPatternLabConfig(
            db_path=settings.crypto_pattern_lab_db_path,
            enabled=settings.crypto_pattern_lab_enabled,
            strategy_paths=settings.crypto_pattern_lab_strategy_paths,
            freqtrade_data_paths=settings.crypto_pattern_lab_freqtrade_data_paths,
            max_symbols=settings.crypto_pattern_lab_max_symbols,
            intervals=settings.crypto_pattern_lab_intervals,
            lookback_bars=settings.crypto_pattern_lab_lookback_bars,
            context_limit=settings.crypto_pattern_lab_context_limit,
            retention_days=settings.crypto_pattern_lab_retention_days,
            backtests_per_tuple_retention=(
                settings.crypto_pattern_lab_backtests_per_tuple_retention
            ),
            optimizer_runs_per_tuple_retention=(
                settings.crypto_pattern_lab_optimizer_runs_per_tuple_retention
            ),
            optimizer_trials_per_run_retention=(
                settings.crypto_pattern_lab_optimizer_trials_per_run_retention
            ),
            max_backtest_rows=settings.crypto_pattern_lab_max_backtest_rows,
            max_optimizer_runs=settings.crypto_pattern_lab_max_optimizer_runs,
            max_optimizer_trials=settings.crypto_pattern_lab_max_optimizer_trials,
            optimizer_enabled=settings.crypto_pattern_lab_optimizer_enabled,
            optimizer_max_scorecards=settings.crypto_pattern_lab_optimizer_max_scorecards,
            optimizer_max_trials_per_scorecard=(
                settings.crypto_pattern_lab_optimizer_max_trials_per_scorecard
            ),
        ),
        kline_reader=HermesKlineReader(settings.crypto_market_research_db_path),
    )


def _clean_pattern_lab_symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    symbol = re.sub(r"[^A-Z0-9]", "", symbol)
    if not re.fullmatch(r"[A-Z0-9]{2,26}USDT", symbol):
        return ""
    return symbol


def select_pattern_lab_symbols(
    settings: Any,
    *,
    max_symbols: int,
) -> tuple[list[str], dict[str, Any]]:
    static_symbols = [
        symbol
        for symbol in (
            _clean_pattern_lab_symbol(row)
            for row in parse_crypto_universe(
                _setting(
                    settings,
                    "crypto_market_research_universe",
                    "BTCUSDT,ETHUSDT,SOLUSDT",
                )
            )
        )
        if symbol
    ]
    dynamic_symbols: list[str] = []
    source_status = "static_only"
    source_error = ""
    research_db_path = str(
        _setting(
            settings,
            "crypto_market_research_db_path",
            ".runtime/crypto_market_research.db",
        )
    )
    try:
        from tradecraft.services.crypto_market_research import (
            CryptoMarketResearchRepository,
        )

        repository = CryptoMarketResearchRepository(research_db_path)
        candidate_rows = repository.latest_candidates(
            limit=max(max_symbols * 4, 20),
            max_age_sec=7 * 24 * 60 * 60,
        )
        feature_rows = repository.latest_features(limit=max(max_symbols * 4, 60))
        dynamic_symbols.extend(
            symbol
            for symbol in (
                _clean_pattern_lab_symbol(row.get("symbol"))
                for row in candidate_rows
            )
            if symbol
        )
        dynamic_symbols.extend(
            symbol
            for symbol in (
                _clean_pattern_lab_symbol(row.get("symbol"))
                for row in feature_rows
            )
            if symbol
        )
        source_status = "research_db"
    except Exception as exc:
        logger.warning("crypto pattern lab dynamic symbol selection failed: %s", exc)
        source_error = str(exc)

    protected_static = static_symbols[: min(len(static_symbols), 3)]
    combined = [
        *protected_static,
        *dynamic_symbols,
        *static_symbols,
    ]
    selected = list(dict.fromkeys(symbol for symbol in combined if symbol))[
        : max(int(max_symbols), 1)
    ]
    return selected, {
        "status": source_status if dynamic_symbols else "static_only",
        "research_db_path": research_db_path,
        "static_count": len(static_symbols),
        "dynamic_count": len(list(dict.fromkeys(dynamic_symbols))),
        "selected_count": len(selected),
        "protected_static": protected_static,
        "error_message": source_error,
    }


async def run_crypto_pattern_lab_loop(
    *,
    settings: Any,
    service: Any | None = None,
    sleep: SleepFn = asyncio.sleep,
) -> None:
    if not bool(_setting(settings, "crypto_pattern_lab_enabled", True)):
        logger.info("crypto pattern lab disabled")
        return
    resolved = service or build_crypto_pattern_lab_service(settings)
    state_path = Path(
        str(_setting(settings, "crypto_pattern_lab_state_path", ".runtime/crypto_pattern_lab.json"))
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    interval = max(int(_setting(settings, "crypto_pattern_lab_interval_sec", 3600)), 60)
    once = bool(_setting(settings, "crypto_pattern_lab_once", False))
    cycle = 0

    while True:
        cycle += 1
        max_symbols = max(int(_setting(settings, "crypto_pattern_lab_max_symbols", 30)), 1)
        symbols, symbol_selection = select_pattern_lab_symbols(
            settings,
            max_symbols=max_symbols,
        )
        try:
            result = resolved.run_once(symbols=symbols)
        except Exception as exc:
            logger.exception("crypto pattern lab cycle failed")
            result = {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }

        prune_result = {"status": "skipped", "reason": "not_supported"}
        prune_history = getattr(resolved, "prune_history", None)
        if prune_history is not None and result.get("status") != "error":
            try:
                prune_result = prune_history()
            except Exception as exc:
                logger.warning("crypto pattern lab retention cleanup failed: %s", exc)
                prune_result = {"status": "error", "error_message": str(exc)}
        elif result.get("status") == "error":
            prune_result = {"status": "skipped", "reason": "cycle_error"}
        try:
            service_status = resolved.status()
        except Exception as exc:
            logger.warning("crypto pattern lab status read failed: %s", exc)
            service_status = {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
        snapshot = {
            "service": "tradecraft-crypto-pattern-lab",
            "status": result.get("status", "ok"),
            "cycle": cycle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "symbol_selection": symbol_selection,
            "result": result,
            "retention": prune_result,
            "service_status": service_status,
        }
        state_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "crypto pattern lab cycle=%s status=%s patterns=%s backtests=%s optimizations=%s",
            cycle,
            result.get("status"),
            result.get("pattern_count"),
            result.get("backtest_count"),
            result.get("optimization_count"),
        )
        if once:
            return
        await sleep(float(interval))


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    write_current_runner_pid("crypto_pattern_lab")
    try:
        try:
            asyncio.run(run_crypto_pattern_lab_loop(settings=AppSettings()))
        except KeyboardInterrupt:
            logger.info("crypto pattern lab runner interrupted; stopping")
    finally:
        clear_current_runner_pid("crypto_pattern_lab")


if __name__ == "__main__":
    run()
