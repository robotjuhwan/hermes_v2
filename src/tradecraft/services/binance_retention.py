from __future__ import annotations

from typing import Any

from tradecraft.services.db_retention import (
    RetentionRule,
    summarize_sqlite_retention_result,
)


def build_binance_operational_retention_rules(
    *,
    quote_retention_days: int,
    manager_run_retention_days: int,
    archive_retention_days: int = 14,
) -> list[RetentionRule]:
    rules: list[RetentionRule] = []
    if int(quote_retention_days) > 0:
        rules.append(
            RetentionRule(
                table="quote_snapshots",
                timestamp_column="fetched_at",
                retention_days=int(quote_retention_days),
                archive_table="quote_snapshots_archive",
                archive_compress_columns=("raw_json",),
                vacuum_after_delete=True,
            )
        )
    if int(manager_run_retention_days) > 0:
        rules.append(
            RetentionRule(
                table="manager_runs",
                timestamp_column="run_at",
                retention_days=int(manager_run_retention_days),
                archive_table="manager_runs_archive",
                archive_compress_columns=(
                    "prompt_json",
                    "response_json",
                    "actions_json",
                ),
                vacuum_after_delete=True,
            )
        )
    if int(archive_retention_days) > 0:
        for table, timestamp_column, source_retention_days in (
            ("quote_snapshots_archive", "fetched_at", int(quote_retention_days)),
            ("manager_runs_archive", "run_at", int(manager_run_retention_days)),
        ):
            rules.append(
                RetentionRule(
                    table=table,
                    timestamp_column=timestamp_column,
                    retention_days=max(source_retention_days, 0)
                    + int(archive_retention_days),
                    vacuum_after_delete=True,
                )
            )
    return rules


def summarize_retention_result(retention: dict[str, Any]) -> dict[str, Any]:
    return summarize_sqlite_retention_result(retention)
