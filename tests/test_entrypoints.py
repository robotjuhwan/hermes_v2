from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFERRED_ENTRYPOINT_TARGETS: dict[str, str] = {}


def _project_scripts() -> dict[str, str]:
    scripts: dict[str, str] = {}
    in_scripts = False
    for raw_line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_scripts = line == "[project.scripts]"
            continue
        if not in_scripts or "=" not in line:
            continue
        name, target = line.split("=", 1)
        scripts[name.strip()] = target.strip().strip('"')
    return scripts


def test_project_script_targets_are_importable() -> None:
    scripts = _project_scripts()

    assert scripts
    for name, target in scripts.items():
        if name in DEFERRED_ENTRYPOINT_TARGETS:
            assert target == DEFERRED_ENTRYPOINT_TARGETS[name]
            continue
        module_name, _, attribute = target.partition(":")
        assert module_name, name
        assert attribute, name

        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), f"{name} -> {target}"


def test_jue_codex_lab_entrypoint_is_retired() -> None:
    scripts = _project_scripts()

    assert "tradecraft-jue-codex-lab" not in scripts
