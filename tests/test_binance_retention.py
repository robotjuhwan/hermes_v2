from __future__ import annotations

from tradecraft.services.binance_retention import (
    build_binance_operational_retention_rules,
    summarize_retention_result,
)


def test_build_binance_operational_retention_rules_archives_raw_payloads() -> None:
    rules = build_binance_operational_retention_rules(
        quote_retention_days=7,
        manager_run_retention_days=30,
        archive_retention_days=45,
    )

    assert [rule.table for rule in rules] == [
        "quote_snapshots",
        "manager_runs",
        "quote_snapshots_archive",
        "manager_runs_archive",
    ]
    quote_rule = rules[0]
    manager_rule = rules[1]
    assert quote_rule.archive_table == "quote_snapshots_archive"
    assert quote_rule.archive_compress_columns == ("raw_json",)
    assert quote_rule.vacuum_after_delete is True
    assert manager_rule.archive_table == "manager_runs_archive"
    assert manager_rule.archive_compress_columns == (
        "prompt_json",
        "response_json",
        "actions_json",
    )
    assert manager_rule.vacuum_after_delete is True
    assert rules[2].timestamp_column == "fetched_at"
    assert rules[2].retention_days == 52
    assert rules[2].archive_table is None
    assert rules[3].timestamp_column == "run_at"
    assert rules[3].retention_days == 75


def test_summarize_retention_result_keeps_existing_binance_payload_shape() -> None:
    summary = summarize_retention_result(
        {
            "status": "ok",
            "tables": {
                "quote_snapshots": {
                    "status": "ok",
                    "deleted": 3,
                    "archived": 3,
                    "compressed": 3,
                    "archive_table": "quote_snapshots_archive",
                },
                "manager_runs": {
                    "status": "skipped",
                    "deleted": 10,
                    "archived": 10,
                    "compressed": 10,
                    "archive_table": "manager_runs_archive",
                },
            },
        }
    )

    assert summary == {
        "status": "ok",
        "deleted": {"quote_snapshots": 3},
        "archived": {"quote_snapshots": 3},
        "compressed": {"quote_snapshots": 3},
        "archive_tables": {"quote_snapshots": "quote_snapshots_archive"},
        "retention": {
            "status": "ok",
            "tables": {
                "quote_snapshots": {
                    "status": "ok",
                    "deleted": 3,
                    "archived": 3,
                    "compressed": 3,
                    "archive_table": "quote_snapshots_archive",
                },
                "manager_runs": {
                    "status": "skipped",
                    "deleted": 10,
                    "archived": 10,
                    "compressed": 10,
                    "archive_table": "manager_runs_archive",
                },
            },
        },
    }
