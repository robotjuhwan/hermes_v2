from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from tradecraft.config import AppSettings
from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.market import mock_dashboard

logger = logging.getLogger(__name__)


def _build_runtime_snapshot(cycle: int) -> dict[str, Any]:
    dashboard = mock_dashboard()
    sessions = list(dashboard.get("sessions", []))
    now = datetime.now(timezone.utc).isoformat()

    for session in sessions:
        session["last_heartbeat"] = now
        if session.get("mode") == "short_term":
            session["trade_count_today"] = int(session.get("trade_count_today") or 0) + (cycle % 2)
            session["status"] = "RUNNING"
        elif session.get("mode") == "mid_long_term":
            session["status"] = "RUNNING"

    return {
        "updated_at": utc_now_iso(),
        "runtime": {
            "service": "tradecraft-runtime",
            "status": "running",
            "cycle": cycle,
        },
        "sessions": sessions,
    }


def run() -> None:
    settings = AppSettings()
    interval = max(int(settings.runtime_write_interval_sec), 1)
    store = RuntimeStateStore(settings.runtime_state_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logger.info("runtime started: state_path=%s interval=%ss", settings.runtime_state_path, interval)

    cycle = 0
    while True:
        cycle += 1
        snapshot = _build_runtime_snapshot(cycle=cycle)
        store.write_snapshot(snapshot)
        logger.info("runtime heartbeat written: cycle=%s sessions=%s", cycle, len(snapshot.get("sessions", [])))
        time.sleep(interval)


if __name__ == "__main__":
    run()
