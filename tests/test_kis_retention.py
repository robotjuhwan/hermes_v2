from __future__ import annotations

from tradecraft.services.kis_retention import (
    build_kis_operational_retention_rules,
    summarize_retention_result,
)


def test_build_kis_operational_retention_rules_archives_raw_payloads() -> None:
    rules = build_kis_operational_retention_rules(
        quote_retention_days=7,
        manager_run_retention_days=30,
        reconciliation_retention_days=14,
        archive_retention_days=45,
    )

    assert [rule.table for rule in rules] == [
        "quote_snapshots",
        "manager_runs",
        "reconciliation_runs",
        "quote_snapshots_archive",
        "manager_runs_archive",
        "reconciliation_runs_archive",
    ]
    quote_rule = rules[0]
    manager_rule = rules[1]
    reconciliation_rule = rules[2]
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
    assert reconciliation_rule.archive_table == "reconciliation_runs_archive"
    assert reconciliation_rule.archive_compress_columns == (
        "account_json",
        "summary_json",
    )
    assert reconciliation_rule.vacuum_after_delete is True
    assert rules[3].timestamp_column == "fetched_at"
    assert rules[3].retention_days == 52
    assert rules[3].archive_table is None
    assert rules[4].timestamp_column == "run_at"
    assert rules[4].retention_days == 75
    assert rules[5].timestamp_column == "run_at"
    assert rules[5].retention_days == 59


def test_summarize_retention_result_keeps_existing_kis_payload_shape() -> None:
    summary = summarize_retention_result(
        {
            "status": "ok",
            "tables": {
                "quote_snapshots": {
                    "status": "ok",
                    "deleted": 2,
                    "archived": 2,
                    "compressed": 2,
                    "archive_table": "quote_snapshots_archive",
                },
                "reconciliation_runs": {
                    "status": "skipped",
                    "deleted": 9,
                    "archived": 9,
                    "compressed": 9,
                    "archive_table": "reconciliation_runs_archive",
                },
            },
        }
    )

    assert summary == {
        "status": "ok",
        "deleted": {"quote_snapshots": 2},
        "archived": {"quote_snapshots": 2},
        "compressed": {"quote_snapshots": 2},
        "archive_tables": {"quote_snapshots": "quote_snapshots_archive"},
        "retention": {
            "status": "ok",
            "tables": {
                "quote_snapshots": {
                    "status": "ok",
                    "deleted": 2,
                    "archived": 2,
                    "compressed": 2,
                    "archive_table": "quote_snapshots_archive",
                },
                "reconciliation_runs": {
                    "status": "skipped",
                    "deleted": 9,
                    "archived": 9,
                    "compressed": 9,
                    "archive_table": "reconciliation_runs_archive",
                },
            },
        },
    }
