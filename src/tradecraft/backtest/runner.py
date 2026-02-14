from __future__ import annotations

import argparse
import logging

from tradecraft.backtest.engine import BacktestConfig, BacktestEngine
from tradecraft.config import AppSettings
from tradecraft.runtime.session_loader import load_runtime_sessions
from tradecraft.runtime.state_store import RuntimeStateStore

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TradeCraft backtest runtime")
    parser.add_argument("--cycles", type=int, default=None, help="simulation cycles")
    parser.add_argument("--speed", type=float, default=None, help="time acceleration multiplier")
    parser.add_argument("--step-sec", type=int, default=None, help="seconds per virtual tick")
    parser.add_argument("--output", type=str, default=None, help="result json path")
    return parser


def run(argv: list[str] | None = None) -> None:
    settings = AppSettings()
    args = _build_parser().parse_args(argv)

    config = BacktestConfig(
        cycles=args.cycles if args.cycles is not None else settings.backtest_cycles,
        step_sec=args.step_sec if args.step_sec is not None else settings.backtest_step_sec,
        speed=args.speed if args.speed is not None else settings.backtest_speed,
        initial_price=settings.backtest_initial_price,
        volatility_bps=settings.backtest_volatility_bps,
        drift_bps=settings.backtest_drift_bps,
        fee_rate=settings.backtest_fee_rate,
        slippage_bps=settings.backtest_slippage_bps,
        seed=settings.backtest_seed,
    )

    session_rows, source = load_runtime_sessions(settings.runtime_sessions_path)
    engine = BacktestEngine.from_session_rows(rows=session_rows, config=config)
    result = engine.run()
    result["backtest"]["session_source"] = source

    output_path = args.output or settings.backtest_result_path
    store = RuntimeStateStore(output_path)
    store.write_snapshot(result)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    logger.info(
        "backtest completed: cycles=%s sessions=%s output=%s",
        result["backtest"]["cycles"],
        result["backtest"]["session_count"],
        output_path,
    )


if __name__ == "__main__":
    run()
