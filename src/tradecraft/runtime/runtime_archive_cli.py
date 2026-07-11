from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from tradecraft.config import AppSettings
from tradecraft.services.jue_wiki_selection_audit import JueWikiSelectionAuditStore
from tradecraft.services.runtime_cold_archive import RuntimeColdArchiveV1
from tradecraft.services.runtime_cold_archive_status import (
    persist_runtime_cold_archive_status,
    read_runtime_cold_archive_status,
)
from tradecraft.services.runtime_maintenance import (
    RuntimeStoragePolicy,
    build_runtime_storage_report,
    cleanup_runtime_storage,
)
from tradecraft.services.runtime_storage_policy import runtime_storage_policy_from_settings


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _print(payload: dict[str, Any]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
    )


def _restricted_policy(args: argparse.Namespace) -> RuntimeStoragePolicy:
    settings = AppSettings()
    base = runtime_storage_policy_from_settings(settings)
    return replace(
        base,
        runtime_dir=str(Path(args.runtime_dir)),
        cold_archive_root=str(Path(args.cold_root)),
        rag_persist_path=str(Path(args.runtime_dir) / "rag_chroma"),
        now_iso=str(getattr(args, "now", "") or ""),
        prune_unreferenced_pdfs=False,
        prune_extracted_report_pdfs=True,
        prune_rag_repair_artifacts=False,
        prune_rag_rebuild_backups=True,
        archive_rag_rebuild_backups=True,
        prune_old_runtime_logs=False,
        prune_duplicate_runtime_logs=False,
        rotate_large_active_logs=False,
        prune_repair_backup_artifacts=True,
        prune_scratch_artifacts=False,
        prune_old_backtest_artifacts=False,
        prune_old_ui_check_artifacts=False,
        prune_old_dryrun_artifacts=True,
        archive_dryrun_artifacts=True,
        prune_zero_byte_runtime_markers=False,
        prune_retired_state_artifacts=False,
        prune_retired_log_artifacts=False,
        prune_retired_db_artifacts=False,
    )


def _selection_migration(
    *,
    wiki_db: Path,
    cold_root: Path,
    apply: bool,
    now: datetime,
) -> dict[str, Any]:
    if not wiki_db.exists():
        return {
            "status": "skipped",
            "reason": "wiki_database_missing",
            "exported_count": 0,
            "deleted_count": 0,
            "entry_ids": [],
        }
    store = JueWikiSelectionAuditStore(wiki_db, cold_root)
    result = store.compact_rejected(
        cutoff=now - timedelta(hours=24),
        apply=apply,
    )
    database_compaction: dict[str, Any] = {
        "status": "skipped",
        "reason": "dry_run" if not apply else "no_deleted_selection_rows",
    }
    if apply and result.deleted_count > 0:
        database_compaction = store.vacuum_hot_database()
    return {
        "status": "ok",
        "dry_run": not apply,
        "verified": bool(result.verified),
        "exported_count": int(result.exported_count),
        "deleted_count": int(result.deleted_count),
        "entry_ids": list(result.entry_ids),
        "database_compaction": database_compaction,
    }


def _parse_now(value: str) -> datetime:
    if not str(value or "").strip():
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-dir", default=".runtime")
    parser.add_argument("--cold-root", default=".runtime-cold-archive")
    parser.add_argument("--wiki-db", default=".runtime/jue_wiki/wiki.db")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tradecraft-runtime-archive")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    _add_paths(status)

    migrate = commands.add_parser("migrate")
    _add_paths(migrate)
    migrate.add_argument("--now", default="")
    migrate.add_argument("--apply", action="store_true")

    verify = commands.add_parser("verify")
    _add_paths(verify)

    restore = commands.add_parser("restore")
    restore.add_argument("entry_id")
    restore.add_argument("destination")
    restore.add_argument("--cold-root", default=".runtime-cold-archive")
    restore.add_argument("--wiki-db", default=".runtime/jue_wiki/wiki.db")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            policy = _restricted_policy(args)
            payload = {
                "status": "ok",
                "runtime_storage": build_runtime_storage_report(policy),
                "cold_archive": read_runtime_cold_archive_status(
                    root=args.cold_root,
                ),
            }
            _print(payload)
            return 0
        if args.command == "migrate":
            policy = _restricted_policy(args)
            runtime_cleanup = cleanup_runtime_storage(
                policy,
                dry_run=not args.apply,
            )
            selection = _selection_migration(
                wiki_db=Path(args.wiki_db),
                cold_root=Path(args.cold_root),
                apply=bool(args.apply),
                now=_parse_now(args.now),
            )
            payload = {
                "status": "warning"
                if runtime_cleanup.get("archive_failures")
                or selection.get("status") == "error"
                else "ok",
                "dry_run": not bool(args.apply),
                "apply": bool(args.apply),
                "runtime_cleanup": runtime_cleanup,
                "wiki_selection": selection,
            }
            if args.apply:
                payload["cold_archive"] = persist_runtime_cold_archive_status(
                    root=args.cold_root,
                    jue_wiki_db_path=args.wiki_db,
                )
            _print(payload)
            return 1 if payload["status"] == "warning" else 0
        if args.command == "verify":
            payload = persist_runtime_cold_archive_status(
                root=args.cold_root,
                jue_wiki_db_path=args.wiki_db,
            )
            _print(payload)
            return 0 if payload["status"] == "ok" else 1
        if args.command == "restore":
            core = RuntimeColdArchiveV1(args.cold_root)
            core_result = core.restore(args.entry_id, Path(args.destination))
            if core_result.reason != "entry_not_found":
                payload = asdict(core_result)
            else:
                selection_result = JueWikiSelectionAuditStore(
                    args.wiki_db,
                    args.cold_root,
                ).restore_partition(args.entry_id, Path(args.destination))
                payload = asdict(selection_result)
            _print(payload)
            return 0 if payload.get("restored") else 1
    except (OSError, ValueError, sqlite3.Error) as exc:
        _print(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        return 1
    return 1


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()
