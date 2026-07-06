#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

GENERATED_TRACKED_PATTERNS = (
    re.compile(r"(^|/)(__pycache__|.*\.pyc$)"),
    re.compile(r"(^|/)\.runtime/"),
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)node_modules/"),
    re.compile(r"(^|/)web_dist/"),
    re.compile(r"(^|/).*\.egg-info/"),
)


def settings_model_field_count() -> int:
    from tradecraft.config import AppSettings

    return len(AppSettings.model_fields)


def docs_config_settings_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"Captured setting names.*?:\s*(\d+)\s+fields", text)
    if not match:
        raise AssertionError(f"captured settings count marker missing: {path}")
    return int(match.group(1))


def project_scripts(root: Path = ROOT) -> dict[str, str]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    return {str(name): str(target) for name, target in scripts.items()}


def tracked_files(root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def tracked_generated_files(root: Path = ROOT) -> list[str]:
    return sorted(
        path
        for path in tracked_files(root)
        if any(pattern.search(path) for pattern in GENERATED_TRACKED_PATTERNS)
    )


def process_status_contract() -> dict[str, Any]:
    from tradecraft.runtime import process_status

    key_sets = {
        "RUNNER_PID_FILES": sorted(process_status.RUNNER_PID_FILES),
        "RUNNER_PATTERNS": sorted(process_status.RUNNER_PATTERNS),
        "RUNNER_LABELS": sorted(process_status.RUNNER_LABELS),
        "RUNNER_RESTART_SPECS": sorted(process_status.RUNNER_RESTART_SPECS),
    }
    all_equal = len({tuple(keys) for keys in key_sets.values()}) == 1
    return {
        "key_sets": key_sets,
        "all_equal": all_equal,
        "default_keys": list(process_status.DEFAULT_RESTART_RUNNER_KEYS),
    }


def validate_all(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    config_spec_path = root / "docs/spec/12_config_env.md"
    try:
        docs_count = docs_config_settings_count(config_spec_path)
    except AssertionError as exc:
        errors.append(str(exc))
    else:
        settings_count = settings_model_field_count()
        if docs_count != settings_count:
            errors.append(
                "docs/spec/12_config_env.md captured settings count "
                f"{docs_count} != AppSettings.model_fields {settings_count}"
            )

    generated = tracked_generated_files(root)
    if generated:
        errors.append("tracked generated files present: " + ", ".join(generated))

    status_contract = process_status_contract()
    if not status_contract["all_equal"]:
        errors.append(
            "process status runner key sets differ: "
            f"{status_contract['key_sets']}"
        )

    return errors


def main() -> int:
    errors = validate_all(ROOT)
    if errors:
        for error in errors:
            print(f"Project contract check FAIL: {error}", file=sys.stderr)
        return 1
    print("Project contract check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
