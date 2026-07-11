from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable


ConnectionFactory = Callable[[], sqlite3.Connection]


def read_binance_repository_status(
    *,
    connect: ConnectionFactory,
    db_path: str | Path,
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
        proposed_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM blocks WHERE status = 'proposed'"
            ).fetchone()[0]
        )
        order_count = int(
            conn.execute("SELECT COUNT(*) FROM block_orders").fetchone()[0]
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
        latest_error_run = conn.execute(
            """
            SELECT run_at, status, mode, error_message
            FROM manager_runs
            WHERE status NOT IN ('ok', 'success') OR error_message != ''
            ORDER BY run_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    latest_manager_error = (
        {
            "run_at": str(latest_error_run["run_at"]),
            "status": str(latest_error_run["status"]),
            "mode": str(latest_error_run["mode"]),
            "error_message": str(latest_error_run["error_message"]),
        }
        if latest_error_run
        else {}
    )
    latest_manager_error_recovered = bool(
        latest_run
        and latest_error_run
        and str(latest_run["status"]).lower() in {"ok", "success"}
        and str(latest_run["run_at"]) > str(latest_error_run["run_at"])
    )
    latest_unresolved_manager_error = (
        {}
        if not latest_manager_error or latest_manager_error_recovered
        else latest_manager_error
    )
    latest_status = str(latest_run["status"]) if latest_run else "missing"
    if latest_status == "missing":
        manager_operational_status = "awaiting_first_manager_run"
    elif latest_status.lower() in {"ok", "success"}:
        manager_operational_status = "ok"
    elif latest_unresolved_manager_error:
        manager_operational_status = "manager_error_pending_next_run"
    else:
        manager_operational_status = "review_latest_manager_run"
    return {
        "status": "ok",
        "db_path": str(db_path),
        "block_count": block_count,
        "open_block_count": open_count,
        "proposed_block_count": proposed_count,
        "order_count": order_count,
        "manager_run_count": manager_count,
        "latest_manager_run_at": str(latest_run["run_at"]) if latest_run else "",
        "latest_manager_status": latest_status,
        "manager_operational_status": manager_operational_status,
        "latest_manager_mode": str(latest_run["mode"]) if latest_run else "",
        "latest_manager_error": latest_manager_error,
        "latest_manager_error_recovered": latest_manager_error_recovered,
        "latest_unresolved_manager_error": latest_unresolved_manager_error,
    }
