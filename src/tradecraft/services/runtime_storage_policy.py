from __future__ import annotations

from pathlib import Path
from typing import Any

from tradecraft.services.runtime_maintenance import RuntimeStoragePolicy


def runtime_storage_policy_from_settings(settings: Any) -> RuntimeStoragePolicy:
    """Build the runtime retention view without exposing mutable app settings."""

    runtime_dir = Path(settings.runtime_state_path).parent
    return RuntimeStoragePolicy(
        runtime_dir=str(runtime_dir or Path(".runtime")),
        cold_archive_root=settings.runtime_cold_archive_root,
        reports_db_path=settings.naver_reports_db_path,
        pdf_archive_dir=settings.naver_reports_pdf_archive_dir,
        rag_persist_path=settings.rag_persist_path,
        large_file_threshold_mb=settings.runtime_storage_large_file_threshold_mb,
        prune_unreferenced_pdfs=settings.runtime_storage_prune_unreferenced_pdfs,
        prune_extracted_report_pdfs=(
            settings.runtime_storage_prune_extracted_report_pdfs
        ),
        extracted_report_pdf_retention_days=(
            settings.runtime_storage_extracted_report_pdf_retention_days
        ),
        prune_rag_repair_artifacts=(
            settings.runtime_storage_prune_rag_repair_artifacts
        ),
        rag_repair_artifact_retention_days=(
            settings.runtime_storage_rag_repair_artifact_retention_days
        ),
        prune_rag_rebuild_backups=(
            settings.runtime_storage_prune_rag_rebuild_backups
        ),
        rag_rebuild_backup_retention_days=(
            settings.runtime_storage_rag_rebuild_backup_retention_days
        ),
        archive_rag_rebuild_backups=(
            settings.runtime_storage_archive_rag_rebuild_backups
        ),
        archive_dryrun_artifacts=settings.runtime_storage_archive_dryrun,
        dryrun_hot_hours=settings.runtime_storage_dryrun_hot_hours,
        dryrun_recent_per_scenario=(
            settings.runtime_storage_dryrun_hot_per_scenario
        ),
        prune_old_runtime_logs=settings.runtime_storage_prune_old_runtime_logs,
        runtime_log_retention_days=settings.runtime_storage_log_retention_days,
        rotate_large_active_logs=settings.runtime_storage_rotate_large_active_logs,
        active_log_max_mb=settings.runtime_storage_active_log_max_mb,
        active_log_tail_kb=settings.runtime_storage_active_log_tail_kb,
        prune_scratch_artifacts=settings.runtime_storage_prune_scratch_artifacts,
        scratch_artifact_retention_days=(
            settings.runtime_storage_scratch_artifact_retention_days
        ),
        prune_old_backtest_artifacts=(
            settings.runtime_storage_prune_old_backtest_artifacts
        ),
        backtest_artifact_retention_days=(
            settings.runtime_storage_backtest_artifact_retention_days
        ),
        prune_old_ui_check_artifacts=(
            settings.runtime_storage_prune_old_ui_check_artifacts
        ),
        ui_check_artifact_retention_days=(
            settings.runtime_storage_ui_check_artifact_retention_days
        ),
        prune_zero_byte_runtime_markers=(
            settings.runtime_storage_prune_zero_byte_runtime_markers
        ),
        zero_byte_marker_retention_days=(
            settings.runtime_storage_zero_byte_marker_retention_days
        ),
        database_compact_min_free_mb=(
            settings.runtime_storage_database_compact_min_free_mb
        ),
        database_compact_min_free_ratio_pct=(
            settings.runtime_storage_database_compact_min_free_ratio_pct
        ),
        archive_retention_days_by_key={
            "kis_blocks": {
                "quote_snapshots_archive": (
                    settings.kis_block_trader_quote_retention_days
                    + settings.kis_block_trader_archive_retention_days
                ),
                "manager_runs_archive": (
                    settings.kis_block_trader_manager_run_retention_days
                    + settings.kis_block_trader_archive_retention_days
                ),
                "reconciliation_runs_archive": (
                    settings.kis_block_trader_reconciliation_retention_days
                    + settings.kis_block_trader_archive_retention_days
                ),
            },
            "binance_blocks": {
                "quote_snapshots_archive": (
                    settings.binance_block_trader_quote_retention_days
                    + settings.binance_block_trader_archive_retention_days
                ),
                "manager_runs_archive": (
                    settings.binance_block_trader_manager_run_retention_days
                    + settings.binance_block_trader_archive_retention_days
                ),
            },
            "market_judgment": {
                "quote_snapshots_archive": (
                    settings.market_judge_quote_archive_retention_days
                ),
                "judgment_runs_archive": (
                    settings.market_judge_judgment_retention_days
                    + settings.market_judge_judgment_archive_retention_days
                ),
                "symbol_judgments_archive": (
                    settings.market_judge_judgment_retention_days
                    + settings.market_judge_judgment_archive_retention_days
                ),
            },
            "market_pulse": settings.market_pulse_archive_retention_days,
            "crypto_market_research": (
                settings.crypto_market_research_archive_retention_days
            ),
            "crypto_quant": settings.crypto_quant_archive_retention_days,
            "etf_research": settings.etf_research_archive_retention_days,
        },
        operational_db_paths=tuple(
            str(runtime_dir / name)
            for name in (
                "crypto_market_research.db",
                "crypto_quant.db",
                "crypto_pattern_lab.db",
                "binance_blocks.db",
                "kis_blocks.db",
                "market_judgment.db",
                "market_pulse.db",
                "investment_memory.db",
                "etf_research.db",
                "strategy_insights.db",
                "trading_validation.db",
                "live_performance.db",
            )
        ),
    )
