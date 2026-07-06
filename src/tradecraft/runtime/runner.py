from __future__ import annotations

import logging
import time

from tradecraft.config import AppSettings
from tradecraft.runtime.engine import RuntimeEngine
from tradecraft.runtime.process_status import (
    clear_current_runner_pid,
    write_current_runner_pid,
)
from tradecraft.runtime.session_loader import load_runtime_sessions
from tradecraft.runtime.state_store import RuntimeStateStore

logger = logging.getLogger(__name__)


def _build_runtime_engine(settings: AppSettings, interval: int) -> tuple[RuntimeEngine, str]:
    session_rows, source = load_runtime_sessions(settings.runtime_sessions_path)
    engine = RuntimeEngine.from_session_rows(
        session_rows,
        base_interval_sec=interval,
        service_name="tradecraft-runtime",
        session_source=source,
    )
    return engine, source


def run() -> None:
    write_current_runner_pid("runtime")
    settings = AppSettings()
    interval = max(int(settings.runtime_write_interval_sec), 1)
    store = RuntimeStateStore(settings.runtime_state_path)
    engine, session_source = _build_runtime_engine(settings, interval)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logger.info(
        "runtime started: state_path=%s interval=%ss sessions=%s",
        settings.runtime_state_path,
        interval,
        engine.session_count,
    )
    logger.info("runtime session source: %s", session_source)

    cycle = 0
    try:
        while True:
            cycle += 1
            snapshot = engine.build_snapshot(cycle=cycle)
            store.write_snapshot(snapshot)
            logger.info(
                "runtime heartbeat written: cycle=%s sessions=%s",
                cycle,
                engine.session_count,
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("runtime state writer interrupted; stopping")
    finally:
        clear_current_runner_pid("runtime")


if __name__ == "__main__":
    run()
