from __future__ import annotations

import atexit
import builtins
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tradecraft.config import AppSettings


_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_LIVE_RUNTIME_ROOT = (_WORKSPACE_ROOT / ".runtime").resolve()
_LIVE_COLD_ARCHIVE_ROOT = (_WORKSPACE_ROOT / ".runtime-cold-archive").resolve()
PYTEST_RUNTIME_ROOT = Path(
    tempfile.mkdtemp(prefix="tradecraft-pytest-runtime-")
).resolve()
PYTEST_COLD_ARCHIVE_ROOT = (PYTEST_RUNTIME_ROOT / "cold-archive").resolve()


def _runtime_relative_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text or "://" in text:
        return None
    path = Path(text).expanduser()
    resolved = (path if path.is_absolute() else _WORKSPACE_ROOT / path).resolve()
    if resolved == _LIVE_RUNTIME_ROOT:
        return Path()
    if _LIVE_RUNTIME_ROOT in resolved.parents:
        return resolved.relative_to(_LIVE_RUNTIME_ROOT)
    if resolved == _LIVE_COLD_ARCHIVE_ROOT:
        return Path("cold-archive")
    if _LIVE_COLD_ARCHIVE_ROOT in resolved.parents:
        return Path("cold-archive") / resolved.relative_to(
            _LIVE_COLD_ARCHIVE_ROOT
        )
    return None


def _field_env_alias(field: Any) -> str:
    candidates: list[str] = []
    for source in (field.alias, field.validation_alias):
        if isinstance(source, str):
            candidates.append(source)
            continue
        candidates.extend(
            choice
            for choice in getattr(source, "choices", ()) or ()
            if isinstance(choice, str)
        )
    for candidate in candidates:
        if candidate.startswith("TRADECRAFT_"):
            return candidate
    return candidates[0] if candidates else ""


def _runtime_path_redirects() -> tuple[tuple[str, ...], dict[str, str]]:
    loaded = AppSettings()
    redirected: list[str] = []
    env_values: dict[str, str] = {}
    for field_name, field in AppSettings.model_fields.items():
        if not field_name.endswith(("_path", "_dir")) and field_name != (
            "runtime_cold_archive_root"
        ):
            continue
        relative = _runtime_relative_path(field.default)
        if relative is None:
            relative = _runtime_relative_path(getattr(loaded, field_name, ""))
        if relative is None:
            continue
        alias = _field_env_alias(field)
        if not alias:
            continue
        target = (PYTEST_RUNTIME_ROOT / relative).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        env_values[alias] = str(target)
        redirected.append(field_name)
    return tuple(sorted(redirected)), env_values


REDIRECTED_RUNTIME_PATH_FIELDS, REDIRECTED_RUNTIME_ENV = _runtime_path_redirects()
REDIRECTED_RUNTIME_ENV["TRADECRAFT_JUE_WIKI_SHADOW_DB_PATH"] = str(
    (PYTEST_RUNTIME_ROOT / "persistent" / "jue_wiki_shadow.db").resolve()
)
REDIRECTED_RUNTIME_ENV["TRADECRAFT_JUE_WIKI_PROVENANCE_KEY_PATH"] = str(
    (PYTEST_RUNTIME_ROOT / "persistent" / "jue_wiki_provenance.key").resolve()
)
REDIRECTED_LIVE_RUNTIME_ACCESSES: set[str] = set()
_TEST_CATEGORY_MARKERS = {"unit", "contract", "integration", "slow"}
_SLOW_TEST_FILE_BYTES = 300_000
_INTEGRATION_FILE_TOKENS = (
    "_api",
    "_runner",
    "admin_auth",
    "runtime",
    "watchdog",
    "microservice",
    "adapter",
    "entrypoints",
    "strategy_intelligence",
)
_CONTRACT_FILE_TOKENS = (
    "contract",
    "payload",
    "config",
    "routes",
    "static_ui",
    "snapshot",
    "settings",
)
_DOMAIN_FILE_TOKENS = {
    "binance": ("binance",),
    "kis": ("kis",),
    "jue": ("jue", "wiki"),
    "readiness": ("readiness", "status", "ops", "system_metrics", "process"),
    "memory": ("memory", "performance", "reflection"),
    "runtime": ("runtime", "runner", "watchdog", "process"),
    "reports": ("report", "research", "rag"),
    "crypto": ("crypto", "binance", "upbit"),
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply deterministic category/domain markers without touching every file."""

    for item in items:
        path = Path(str(item.path))
        file_name = path.name.lower()
        explicit_categories = {
            marker.name
            for marker in item.iter_markers()
            if marker.name in _TEST_CATEGORY_MARKERS
        }
        if not explicit_categories:
            try:
                file_size = path.stat().st_size
            except OSError:
                file_size = 0
            if file_size >= _SLOW_TEST_FILE_BYTES:
                category = "slow"
            elif any(token in file_name for token in _INTEGRATION_FILE_TOKENS):
                category = "integration"
            elif any(token in file_name for token in _CONTRACT_FILE_TOKENS):
                category = "contract"
            else:
                category = "unit"
            item.add_marker(getattr(pytest.mark, category))
        for domain, tokens in _DOMAIN_FILE_TOKENS.items():
            if any(token in file_name for token in tokens):
                item.add_marker(getattr(pytest.mark, domain))


def _redirect_live_runtime_path(value: object) -> object:
    if isinstance(value, int):
        return value
    try:
        raw = os.fspath(value)
    except TypeError:
        return value
    text = os.fsdecode(raw).strip()
    if not text or "://" in text:
        return value
    path = Path(text).expanduser()
    resolved = Path(os.path.abspath(path if path.is_absolute() else Path.cwd() / path))
    redirected: Path | None = None
    for live_root, test_root in (
        (_LIVE_RUNTIME_ROOT, PYTEST_RUNTIME_ROOT),
        (_LIVE_COLD_ARCHIVE_ROOT, PYTEST_COLD_ARCHIVE_ROOT),
    ):
        if resolved == live_root:
            redirected = test_root
            break
        if live_root in resolved.parents:
            redirected = (test_root / resolved.relative_to(live_root)).resolve()
            break
    if redirected is None:
        return value
    redirected.parent.mkdir(parents=True, exist_ok=True)
    REDIRECTED_LIVE_RUNTIME_ACCESSES.add(str(resolved))
    if isinstance(raw, bytes):
        return os.fsencode(redirected)
    if isinstance(value, Path):
        return redirected
    return str(redirected)


def _import_main_with_isolated_runtime() -> None:
    previous = {key: os.environ.get(key) for key in REDIRECTED_RUNTIME_ENV}
    os.environ.update(REDIRECTED_RUNTIME_ENV)
    try:
        __import__("tradecraft.main")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


_import_main_with_isolated_runtime()
atexit.register(shutil.rmtree, PYTEST_RUNTIME_ROOT, True)


def _is_live_llm_usage_path(value: str | Path) -> bool:
    path = Path(value)
    live_path = Path(".runtime/llm_usage.db")
    live_candidates = {
        str(live_path),
        str(live_path.expanduser()),
        str(live_path.expanduser().resolve()),
        str((Path.cwd() / live_path).expanduser().resolve()),
    }
    try:
        candidate = str(path.expanduser().resolve())
    except OSError:
        candidate = str(path)
    return str(value) in live_candidates or candidate in live_candidates


@pytest.fixture(autouse=True)
def isolate_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Redirect mutable runtime paths while preserving config default tests."""

    test_file = Path(str(request.node.path)).name
    if test_file == "test_config.py":
        return
    for alias, target in REDIRECTED_RUNTIME_ENV.items():
        monkeypatch.setenv(alias, target)


@pytest.fixture(autouse=True)
def forbid_live_runtime_file_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect direct live-runtime opens that bypass AppSettings aliases."""

    original_builtin_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_pathlib_open = Path()._accessor.open
    original_pathlib_stat = Path()._accessor.stat
    original_pathlib_listdir = Path()._accessor.listdir
    original_pathlib_scandir = Path()._accessor.scandir
    original_pathlib_mkdir = Path()._accessor.mkdir
    original_pathlib_touch = Path()._accessor.touch
    original_pathlib_unlink = Path()._accessor.unlink
    original_pathlib_rmdir = Path()._accessor.rmdir
    original_pathlib_rename = Path()._accessor.rename
    original_pathlib_replace = Path()._accessor.replace
    original_sqlite_connect = sqlite3.connect

    def guarded_builtin_open(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_builtin_open(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_io_open(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_io_open(_redirect_live_runtime_path(file), *args, **kwargs)

    def guarded_os_open(file: object, *args: Any, **kwargs: Any) -> int:
        return original_os_open(_redirect_live_runtime_path(file), *args, **kwargs)

    def guarded_pathlib_open(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_open(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_stat(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_stat(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_listdir(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_listdir(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_scandir(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_scandir(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_mkdir(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_mkdir(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_touch(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_touch(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_unlink(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_unlink(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_rmdir(file: object, *args: Any, **kwargs: Any) -> Any:
        return original_pathlib_rmdir(
            _redirect_live_runtime_path(file),
            *args,
            **kwargs,
        )

    def guarded_pathlib_rename(
        source: object,
        target: object,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return original_pathlib_rename(
            _redirect_live_runtime_path(source),
            _redirect_live_runtime_path(target),
            *args,
            **kwargs,
        )

    def guarded_pathlib_replace(
        source: object,
        target: object,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return original_pathlib_replace(
            _redirect_live_runtime_path(source),
            _redirect_live_runtime_path(target),
            *args,
            **kwargs,
        )

    def guarded_sqlite_connect(database: object, *args: Any, **kwargs: Any) -> Any:
        return original_sqlite_connect(
            _redirect_live_runtime_path(database),
            *args,
            **kwargs,
        )

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(
        type(Path()._accessor),
        "open",
        staticmethod(guarded_pathlib_open),
    )
    monkeypatch.setattr(
        type(Path()._accessor),
        "stat",
        staticmethod(guarded_pathlib_stat),
    )
    for name, guarded in (
        ("listdir", guarded_pathlib_listdir),
        ("scandir", guarded_pathlib_scandir),
        ("mkdir", guarded_pathlib_mkdir),
        ("touch", guarded_pathlib_touch),
        ("unlink", guarded_pathlib_unlink),
        ("rmdir", guarded_pathlib_rmdir),
        ("rename", guarded_pathlib_rename),
        ("replace", guarded_pathlib_replace),
    ):
        monkeypatch.setattr(
            type(Path()._accessor),
            name,
            staticmethod(guarded),
        )
    monkeypatch.setattr(sqlite3, "connect", guarded_sqlite_connect)


@pytest.fixture(autouse=True)
def isolate_runtime_llm_usage_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep API tests from writing LLM telemetry into the live runtime DB."""

    usage_db_path = str(tmp_path / "llm_usage.db")
    monkeypatch.setenv("TRADECRAFT_LLM_USAGE_DB_PATH", usage_db_path)

    from tradecraft.services import llm_usage as llm_usage_module

    original_init = llm_usage_module.LLMUsageRepository.__init__

    def init_with_isolated_live_db(
        self: llm_usage_module.LLMUsageRepository,
        path: str,
    ) -> None:
        target = usage_db_path if _is_live_llm_usage_path(path) else path
        original_init(self, target)

    monkeypatch.setattr(
        llm_usage_module.LLMUsageRepository,
        "__init__",
        init_with_isolated_live_db,
    )

    main_module = sys.modules.get("tradecraft.main")
    if main_module is None:
        return

    status_pool = getattr(main_module, "_ops_status_provider_pool", None)
    if status_pool is not None and callable(getattr(status_pool, "clear", None)):
        status_pool.clear()

    settings = getattr(main_module, "settings", None)
    if settings is not None:
        monkeypatch.setattr(settings, "llm_usage_db_path", usage_db_path, raising=False)

    def patch_config(obj: Any) -> None:
        config = getattr(obj, "config", None)
        if config is None:
            return
        if hasattr(config, "usage_db_path"):
            monkeypatch.setattr(config, "usage_db_path", usage_db_path, raising=False)
        if hasattr(config, "llm_usage_db_path"):
            monkeypatch.setattr(config, "llm_usage_db_path", usage_db_path, raising=False)

    for value in vars(main_module).values():
        patch_config(value)
        patch_config(getattr(value, "codex_runtime", None))
