from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from tradecraft.services.jue_language_policy import jue_language_policy


REQUIRED_SKILL_KEYS = {
    "skill_id",
    "name",
    "version",
    "scope",
    "source_inspiration",
    "required_outputs",
    "max_prompt_chars",
}
REQUIRED_WORKFLOW_KEYS = {
    "workflow_id",
    "version",
    "scope",
    "model_policy",
    "cadence",
    "required_skills",
    "required_context",
    "output_contracts",
    "authority",
    "safety_gates",
    "prompt_budget",
}
LIST_SKILL_KEYS = {"source_inspiration", "required_outputs"}
INTEGER_SKILL_KEYS = {"version", "max_prompt_chars"}


class JueSkillValidationError(ValueError):
    """Raised when Jue skill/workflow assets are malformed."""


@dataclass(frozen=True)
class JueSkillRegistry:
    root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1] / "jue")

    def load_skill(self, skill_id: str) -> dict[str, Any]:
        path = self.root / "skills" / f"{skill_id}.md"
        if not path.exists():
            raise JueSkillValidationError(f"skill not found: {skill_id}")
        skill = parse_skill_markdown(path.read_text(encoding="utf-8"))
        if skill.get("skill_id") != skill_id:
            raise JueSkillValidationError(f"skill id mismatch: {skill_id}")
        return skill

    def load_contract(self, contract_id: str) -> dict[str, Any]:
        path = self.root / "contracts" / f"{contract_id}.json"
        if not path.exists():
            raise JueSkillValidationError(f"contract not found: {contract_id}")
        data = _load_json_asset(path, f"contract {contract_id}")
        if data.get("contract_id") != contract_id:
            raise JueSkillValidationError(f"contract id mismatch: {contract_id}")
        if _positive_int(data.get("version"), f"contract {contract_id} version") <= 0:
            raise JueSkillValidationError(f"contract version missing: {contract_id}")
        _require_string_list(data, "required", f"contract {contract_id}")
        _require_string_list(data, "reject_when", f"contract {contract_id}")
        return data

    def load_source_manifest(self, source_id: str) -> dict[str, Any]:
        path = self.root / "sources" / f"{source_id}_manifest.json"
        if not path.exists():
            path = self.root / "sources" / f"{source_id}.json"
        if not path.exists():
            raise JueSkillValidationError(f"source manifest not found: {source_id}")
        data = _load_json_asset(path, f"source manifest {source_id}")
        if data.get("source_id") != source_id:
            raise JueSkillValidationError(f"source manifest id mismatch: {source_id}")
        mappings = data.get("mappings")
        if not isinstance(mappings, list) or not mappings:
            raise JueSkillValidationError(f"source manifest mappings missing: {source_id}")
        return data

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        path = self.root / "workflows" / f"{workflow_id}.json"
        if not path.exists():
            raise JueSkillValidationError(f"workflow not found: {workflow_id}")
        data = _load_json_asset(path, f"workflow {workflow_id}")
        missing = sorted(REQUIRED_WORKFLOW_KEYS - set(data))
        if missing:
            raise JueSkillValidationError(
                f"missing required workflow metadata for {workflow_id}: {', '.join(missing)}"
            )
        if data.get("workflow_id") != workflow_id:
            raise JueSkillValidationError(f"workflow id mismatch: {workflow_id}")
        if _positive_int(data.get("version"), f"workflow {workflow_id} version") <= 0:
            raise JueSkillValidationError(f"workflow version missing: {workflow_id}")
        for key in ("required_skills", "required_context", "output_contracts", "safety_gates"):
            _require_string_list(data, key, f"workflow {workflow_id}")
        if not isinstance(data.get("model_policy"), dict):
            raise JueSkillValidationError(f"workflow model_policy malformed: {workflow_id}")
        if not isinstance(data.get("cadence"), dict):
            raise JueSkillValidationError(f"workflow cadence malformed: {workflow_id}")
        if not isinstance(data.get("authority"), dict):
            raise JueSkillValidationError(f"workflow authority malformed: {workflow_id}")
        if not isinstance(data.get("prompt_budget"), dict):
            raise JueSkillValidationError(f"workflow prompt_budget malformed: {workflow_id}")
        return data

    def compile_prompt_pack(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.load_workflow(workflow_id)
        budget = workflow.get("prompt_budget") or {}
        max_skill_chars = max(
            _positive_int(
                budget.get("max_skill_chars"),
                f"workflow {workflow_id} prompt_budget.max_skill_chars",
            ),
            1,
        )
        max_contract_chars = max(
            _positive_int(
                budget.get("max_contract_chars"),
                f"workflow {workflow_id} prompt_budget.max_contract_chars",
            ),
            2,
        )
        skills = [self.load_skill(skill_id) for skill_id in workflow.get("required_skills") or []]
        contracts = [
            self.load_contract(contract_id)
            for contract_id in workflow.get("output_contracts") or []
        ]
        skill_ids = [skill["skill_id"] for skill in skills]
        compact_skills = _compact_skills(skills, max_chars=max_skill_chars)
        compact_contracts = _compact_contracts(contracts, max_chars=max_contract_chars)
        used_contract_chars = len(
            json.dumps(compact_contracts, ensure_ascii=False, sort_keys=True)
        )
        return {
            "workflow_id": workflow["workflow_id"],
            "workflow_version": workflow["version"],
            "scope": workflow["scope"],
            "model_policy": workflow["model_policy"],
            "cadence": workflow["cadence"],
            "required_context": workflow["required_context"],
            "skills": compact_skills,
            "contracts": compact_contracts,
            "source_manifest_links": self._source_links_for_skills(skill_ids),
            "language_policy": jue_language_policy(),
            "authority": workflow["authority"],
            "safety_gates": workflow["safety_gates"],
            "prompt_budget": {
                **budget,
                "used_skill_chars": sum(
                    len(str(row.get("content_md") or "")) for row in compact_skills
                ),
                "used_contract_chars": used_contract_chars,
            },
        }

    def _source_links_for_skills(self, skill_ids: list[str]) -> list[dict[str, Any]]:
        source_dir = self.root / "sources"
        if not source_dir.exists():
            default_root = Path(__file__).resolve().parents[1] / "jue"
            if self.root.resolve() == default_root.resolve():
                raise JueSkillValidationError("source manifest directory missing")
            return []
        manifest = self.load_source_manifest("financial_services")
        links: list[dict[str, Any]] = []
        for row in manifest.get("mappings") or []:
            if row.get("local_skill_id") in skill_ids:
                links.append(
                    {
                        "source_id": manifest["source_id"],
                        "source_skill": row.get("source_skill"),
                        "source_url": row.get("source_url"),
                        "local_skill_id": row.get("local_skill_id"),
                        "adopted_principles": list(row.get("adopted_principles") or [])[:8],
                    }
                )
        return links


def parse_skill_markdown(text: str) -> dict[str, Any]:
    frontmatter, body = _split_frontmatter(text)
    meta = _parse_limited_frontmatter(frontmatter)
    missing = sorted(REQUIRED_SKILL_KEYS - set(meta))
    if missing:
        raise JueSkillValidationError(
            f"missing required skill metadata: {', '.join(missing)}"
        )
    for key in LIST_SKILL_KEYS:
        _require_string_list(meta, key, "skill metadata")
    for key in INTEGER_SKILL_KEYS:
        try:
            meta[key] = int(meta[key])
        except (TypeError, ValueError) as exc:
            raise JueSkillValidationError(f"skill metadata integer malformed: {key}") from exc
        if meta[key] <= 0:
            raise JueSkillValidationError(f"skill metadata integer must be positive: {key}")
    meta["content_md"] = body.strip()
    if not meta["content_md"]:
        raise JueSkillValidationError(f"skill content missing: {meta.get('skill_id')}")
    return meta


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise JueSkillValidationError("skill frontmatter missing")
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise JueSkillValidationError("skill frontmatter malformed")


def _parse_limited_frontmatter(frontmatter: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key = ""
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - "):
            if not current_list_key:
                raise JueSkillValidationError("frontmatter list item without key")
            result[current_list_key].append(line[4:].strip())
            continue
        if raw_line.startswith(" "):
            raise JueSkillValidationError(f"unsupported frontmatter structure: {line}")
        if ":" not in line:
            raise JueSkillValidationError(f"frontmatter line malformed: {line}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise JueSkillValidationError("frontmatter key missing")
        if key in result:
            raise JueSkillValidationError(f"frontmatter duplicate key: {key}")
        current_list_key = ""
        if raw_value == "":
            result[key] = []
            current_list_key = key
        else:
            if raw_value.startswith(("[", "{")):
                raise JueSkillValidationError(f"unsupported frontmatter value: {key}")
            result[key] = raw_value
    return result


def _compact_skills(skills: list[dict[str, Any]], *, max_chars: int) -> list[dict[str, Any]]:
    remaining = max_chars
    rows: list[dict[str, Any]] = []
    for skill in skills:
        content = str(skill.get("content_md") or "")
        per_skill_limit = int(skill.get("max_prompt_chars") or remaining)
        allowed = max(min(per_skill_limit, remaining), 0)
        compact_content = content[:allowed]
        remaining -= len(compact_content)
        rows.append(
            {
                "skill_id": skill["skill_id"],
                "name": skill["name"],
                "version": skill["version"],
                "scope": skill["scope"],
                "required_outputs": skill["required_outputs"],
                "source_inspiration": skill["source_inspiration"],
                "content_md": compact_content,
                "truncated": len(compact_content) < len(content),
            }
        )
    return rows


def _compact_contracts(contracts: list[dict[str, Any]], *, max_chars: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    remaining = max_chars
    for contract in contracts:
        text = json.dumps(contract, ensure_ascii=False, sort_keys=True)
        proposed = [*rows, contract]
        proposed_text = json.dumps(proposed, ensure_ascii=False, sort_keys=True)
        if len(proposed_text) > max_chars:
            summary_budget = _remaining_list_item_budget(rows, max_chars=max_chars)
            summary = _contract_summary(contract, max_chars=summary_budget)
            if summary and _serialized_contract_list_len([*rows, summary]) <= max_chars:
                rows.append(summary)
            break
        rows.append(contract)
        remaining -= len(text)
    return rows


def _contract_summary(contract: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    if max_chars <= 2:
        return {}
    summary: dict[str, Any] = {
        "contract_id": str(contract.get("contract_id") or ""),
        "version": contract.get("version"),
        "truncated": True,
        "required": list(contract.get("required") or []),
        "reject_when": list(contract.get("reject_when") or []),
    }
    while summary["reject_when"]:
        text = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        if len(text) <= max_chars:
            return summary
        summary["reject_when"] = summary["reject_when"][:-1]
    while summary["required"]:
        text = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        if len(text) <= max_chars:
            return summary
        summary["required"] = summary["required"][:-1]
    minimal = {
        "contract_id": summary["contract_id"],
        "version": summary["version"],
        "truncated": True,
    }
    text = json.dumps(minimal, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return minimal
    tiny = {
        "contract_id": summary["contract_id"][: max(max_chars - 44, 0)],
        "truncated": True,
    }
    text = json.dumps(tiny, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return tiny
    return {}


def _remaining_list_item_budget(rows: list[dict[str, Any]], *, max_chars: int) -> int:
    if not rows:
        return max(max_chars - 2, 0)
    existing_len = _serialized_contract_list_len(rows)
    # Replacing the final closing bracket with comma + item + closing bracket
    # adds two separator characters around the serialized item.
    return max(max_chars - existing_len - 1, 0)


def _serialized_contract_list_len(rows: list[dict[str, Any]]) -> int:
    return len(json.dumps(rows, ensure_ascii=False, sort_keys=True))


def _require_string_list(data: dict[str, Any], key: str, label: str) -> None:
    value = data.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(row, str) for row in value):
        raise JueSkillValidationError(f"{label} requires non-empty string list: {key}")


def _load_json_asset(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JueSkillValidationError(
            f"{label} json malformed at {path}: {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise JueSkillValidationError(f"{label} json root must be object: {path}")
    return data


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise JueSkillValidationError(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise JueSkillValidationError(f"{label} must be a positive integer")
    return parsed
