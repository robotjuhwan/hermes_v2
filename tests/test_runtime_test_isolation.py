from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import conftest

from tradecraft.config import AppSettings


def test_pytest_redirects_loaded_runtime_paths_before_test_imports() -> None:
    runtime_root = conftest.PYTEST_RUNTIME_ROOT
    live_runtime_root = Path(".runtime").resolve()
    redirected_fields = conftest.REDIRECTED_RUNTIME_PATH_FIELDS
    settings = AppSettings()

    assert runtime_root.is_absolute()
    assert redirected_fields
    assert "binance_block_trader_db_path" in redirected_fields
    assert "kis_block_trader_db_path" in redirected_fields
    assert "jue_wiki_db_path" in redirected_fields
    assert "runtime_cold_archive_root" in redirected_fields

    for field_name in redirected_fields:
        configured = Path(str(getattr(settings, field_name))).expanduser().resolve()
        assert configured != live_runtime_root
        assert live_runtime_root not in configured.parents
        if field_name != "llm_usage_db_path":
            assert configured == runtime_root or runtime_root in configured.parents


def test_pytest_redirects_cold_archive_settings_and_direct_file_access() -> None:
    settings = AppSettings()
    configured = Path(settings.runtime_cold_archive_root).expanduser().resolve()
    live_cold_root = Path(".runtime-cold-archive").resolve()
    redirected_file = conftest.PYTEST_COLD_ARCHIVE_ROOT / "isolation-check.json"
    live_file = live_cold_root / "isolation-check.json"

    assert configured == conftest.PYTEST_COLD_ARCHIVE_ROOT
    assert configured != live_cold_root

    live_file.write_text('{"isolated": true}', encoding="utf-8")

    assert redirected_file.read_text(encoding="utf-8") == '{"isolated": true}'
    assert str(live_file) in conftest.REDIRECTED_LIVE_RUNTIME_ACCESSES


def test_tradecraft_main_is_constructed_with_isolated_runtime_paths() -> None:
    import tradecraft.main as main_module

    runtime_root = conftest.PYTEST_RUNTIME_ROOT
    live_runtime_root = Path(".runtime").resolve()

    for field_name in conftest.REDIRECTED_RUNTIME_PATH_FIELDS:
        configured = Path(
            str(getattr(main_module.settings, field_name))
        ).expanduser().resolve()
        assert configured != live_runtime_root
        assert live_runtime_root not in configured.parents
        if field_name != "llm_usage_db_path":
            assert configured == runtime_root or runtime_root in configured.parents


def test_pytest_redirects_direct_live_runtime_file_and_sqlite_access() -> None:
    live_file = Path(".runtime") / "pytest-must-not-touch.txt"
    live_db = Path(".runtime") / "pytest-must-not-touch.db"
    redirected_file = conftest.PYTEST_RUNTIME_ROOT / live_file.name
    redirected_db = conftest.PYTEST_RUNTIME_ROOT / live_db.name

    live_file.write_text("isolated", encoding="utf-8")
    with sqlite3.connect(live_db) as conn:
        conn.execute("CREATE TABLE isolation_check (id INTEGER PRIMARY KEY)")

    assert redirected_file.read_text(encoding="utf-8") == "isolated"
    assert redirected_db.exists()
    assert str(Path(os.path.abspath(live_file))) in (
        conftest.REDIRECTED_LIVE_RUNTIME_ACCESSES
    )
    assert str(Path(os.path.abspath(live_db))) in (
        conftest.REDIRECTED_LIVE_RUNTIME_ACCESSES
    )


def test_pytest_redirects_direct_live_runtime_directory_mutations() -> None:
    live_dir = Path(".runtime") / "pytest-must-not-touch-dir"
    redirected_dir = conftest.PYTEST_RUNTIME_ROOT / live_dir.name

    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "marker.txt").touch()

    assert redirected_dir.is_dir()
    assert (redirected_dir / "marker.txt").exists()
    assert str(Path(os.path.abspath(live_dir))) in (
        conftest.REDIRECTED_LIVE_RUNTIME_ACCESSES
    )
