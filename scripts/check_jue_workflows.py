#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tradecraft.services.jue_skill_registry import (  # noqa: E402
    JueSkillRegistry,
    JueSkillValidationError,
)


def main() -> int:
    root = ROOT / "src" / "tradecraft" / "jue"
    registry = JueSkillRegistry(root=root)
    errors: list[str] = []
    for workflow_path in sorted((root / "workflows").glob("*.json")):
        workflow_id = workflow_path.stem
        try:
            registry.compile_prompt_pack(workflow_id)
        except (JueSkillValidationError, ValueError, OSError) as exc:
            errors.append(f"{workflow_id}: {exc}")
    for skill_path in sorted((root / "skills").glob("*.md")):
        try:
            registry.load_skill(skill_path.stem)
        except (JueSkillValidationError, ValueError, OSError) as exc:
            errors.append(f"{skill_path.name}: {exc}")
    try:
        manifest = registry.load_source_manifest("financial_services")
        for row in manifest["mappings"]:
            local_skill_id = str(row.get("local_skill_id") or "")
            try:
                registry.load_skill(local_skill_id)
            except (JueSkillValidationError, ValueError, OSError) as exc:
                errors.append(f"financial_services:{local_skill_id}: {exc}")
    except (JueSkillValidationError, ValueError, OSError) as exc:
        errors.append(f"financial_services: {exc}")
    if errors:
        for error in errors:
            print(f"Jue workflow check FAIL: {error}", file=sys.stderr)
        return 1
    print("Jue workflow check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
