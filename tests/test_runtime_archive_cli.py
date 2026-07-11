from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tradecraft.runtime.runtime_archive_cli import main


def _old_dryrun_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime_dir = tmp_path / ".runtime"
    dryrun_dir = runtime_dir / "dryrun"
    dryrun_dir.mkdir(parents=True)
    database = dryrun_dir / "binance_blocks_rehearsal4.db"
    state = dryrun_dir / "binance_block_trader_rehearsal4.json"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO orders DEFAULT VALUES")
    state.write_text('{"status":"done"}', encoding="utf-8")
    timestamp = datetime(2026, 7, 9, tzinfo=timezone.utc).timestamp()
    os.utime(database, (timestamp, timestamp))
    os.utime(state, (timestamp, timestamp))
    return runtime_dir, database, state


def test_migrate_defaults_to_dry_run(tmp_path: Path, capsys) -> None:
    runtime_dir, database, state = _old_dryrun_bundle(tmp_path)
    cold_root = tmp_path / "cold"

    exit_code = main(
        [
            "migrate",
            "--runtime-dir",
            str(runtime_dir),
            "--cold-root",
            str(cold_root),
            "--wiki-db",
            str(runtime_dir / "missing-wiki.db"),
            "--now",
            "2026-07-11T00:00:00Z",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["dry_run"] is True
    assert payload["runtime_cleanup"]["archive_candidates"]
    assert database.exists() and state.exists()
    assert not cold_root.exists()


def test_migrate_apply_archives_verified_bundle(tmp_path: Path, capsys) -> None:
    runtime_dir, database, state = _old_dryrun_bundle(tmp_path)
    cold_root = tmp_path / "cold"

    exit_code = main(
        [
            "migrate",
            "--runtime-dir",
            str(runtime_dir),
            "--cold-root",
            str(cold_root),
            "--wiki-db",
            str(runtime_dir / "missing-wiki.db"),
            "--now",
            "2026-07-11T00:00:00Z",
            "--apply",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["dry_run"] is False
    assert payload["runtime_cleanup"]["archived"][0]["verified"] is True
    assert not database.exists() and not state.exists()
    assert (cold_root / "manifest-v1.json").exists()


def test_restore_refuses_nonempty_destination(tmp_path: Path, capsys) -> None:
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "existing").write_text("keep", encoding="utf-8")

    exit_code = main(
        [
            "restore",
            "missing-entry",
            str(destination),
            "--cold-root",
            str(tmp_path / "cold"),
            "--wiki-db",
            str(tmp_path / "wiki.db"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["restored"] is False
    assert (destination / "existing").read_text(encoding="utf-8") == "keep"
