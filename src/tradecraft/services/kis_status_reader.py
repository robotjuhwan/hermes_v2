from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable


ConnectionFactory = Callable[[], sqlite3.Connection]


def read_kis_repository_status(
    *,
    connect: ConnectionFactory,
    db_path: str | Path,
    kill_switch: Any,
) -> dict[str, object]:
    with connect() as conn:
        block_count = int(conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])
        open_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM blocks
                WHERE status IN ('entry_pending','open','exit_pending')
                """
            ).fetchone()[0]
        )
        waiting_entry_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM blocks WHERE status = 'proposed'"
            ).fetchone()[0]
        )
        order_count = int(
            conn.execute("SELECT COUNT(*) FROM block_orders").fetchone()[0]
        )
        pending_order_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM block_orders
                WHERE status IN ('sent','partially_filled','cancel_requested')
                """
            ).fetchone()[0]
        )
        manager_count = int(
            conn.execute("SELECT COUNT(*) FROM manager_runs").fetchone()[0]
        )
        latest_run = conn.execute(
            """
            SELECT run_at, status, mode
            FROM manager_runs
            ORDER BY run_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return {
        "status": "ok",
        "db_path": str(db_path),
        "block_count": block_count,
        "open_block_count": open_count,
        "waiting_entry_block_count": waiting_entry_count,
        "order_count": order_count,
        "pending_order_count": pending_order_count,
        "manager_run_count": manager_count,
        "latest_manager_run_at": str(latest_run["run_at"]) if latest_run else "",
        "latest_manager_status": (
            str(latest_run["status"]) if latest_run else "missing"
        ),
        "latest_manager_mode": str(latest_run["mode"]) if latest_run else "",
        "kill_switch": (
            kill_switch if isinstance(kill_switch, dict) else {"enabled": False}
        ),
    }
