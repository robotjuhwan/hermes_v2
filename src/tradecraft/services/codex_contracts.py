from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from tradecraft.services.jue_skill_registry import JueSkillRegistry


def _string_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _field_schema(name: str) -> dict[str, Any]:
    lower = name.lower()
    if any(token in lower for token in ("price", "confidence", "score", "budget")):
        return {"type": ["number", "string"]}
    if any(token in lower for token in ("picks", "signals")):
        return _string_array()
    if any(token in lower for token in ("refs", "reasons", "risks", "gaps")):
        return _string_array()
    return {"type": "string"}


@dataclass(frozen=True)
class CodexContractSchemaLoader:
    registry: JueSkillRegistry = field(default_factory=JueSkillRegistry)

    def schema_for_contract_ids(self, contract_ids: list[str]) -> dict[str, Any] | None:
        ids = [str(value or "").strip() for value in contract_ids if str(value or "").strip()]
        if not ids:
            return None
        ids = list(dict.fromkeys(ids))
        if len(ids) == 1:
            return self._schema_for_contract(ids[0])
        contract_schemas = [self._schema_for_contract(contract_id) for contract_id in ids]
        payload_properties: dict[str, Any] = {
            "contract_id": {"type": "string", "enum": ids},
            "version": {"type": "integer"},
        }
        for schema in contract_schemas:
            properties = schema.get("properties")
            if not isinstance(properties, dict):
                continue
            for key, value in properties.items():
                if key == "contract_id":
                    continue
                payload_properties.setdefault(str(key), value)
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selected_contract_id": {"type": "string", "enum": ids},
                "payload": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": payload_properties,
                    "required": list(payload_properties.keys()),
                },
            },
            "required": ["selected_contract_id", "payload"],
        }

    def _schema_for_contract(self, contract_id: str) -> dict[str, Any]:
        contract = self.registry.load_contract(contract_id)
        required_names = [str(value) for value in contract.get("required") or []]
        properties: dict[str, Any] = {
            "contract_id": {"type": "string", "enum": [contract_id]},
            "version": {"type": "integer"},
            "decision": {"type": "string"},
            "reasons": _string_array(),
            "risks": _string_array(),
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source": {"type": "string"},
                        "claim": {"type": "string"},
                        "as_of": {"type": "string"},
                    },
                    "required": ["source", "claim", "as_of"],
                },
            },
            "data_gaps": _string_array(),
            "next_actions": _string_array(),
        }
        for name in required_names:
            properties.setdefault(name, _field_schema(name))
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties.keys()),
        }
