from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


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
