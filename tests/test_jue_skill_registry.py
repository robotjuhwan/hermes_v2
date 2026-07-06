from __future__ import annotations

import os
from pathlib import Path

import pytest

from tradecraft.services.jue_skill_registry import (
    JueSkillRegistry,
    JueSkillValidationError,
    parse_skill_markdown,
)


def test_parse_skill_markdown_extracts_frontmatter_and_body() -> None:
    text = """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Always produce entry, target, stop, horizon, sizing reason, and invalidation.
"""

    skill = parse_skill_markdown(text)

    assert skill["skill_id"] == "block_design"
    assert skill["name"] == "Block Design"
    assert skill["version"] == 1
    assert skill["scope"] == "shared"
    assert skill["source_inspiration"] == ["https://github.com/anthropics/financial-services"]
    assert skill["required_outputs"] == ["executable_block"]
    assert "Always produce entry" in skill["content_md"]


def test_parse_skill_markdown_rejects_missing_required_keys() -> None:
    text = """---
skill_id: broken
version: 1
---
# Broken
"""

    with pytest.raises(JueSkillValidationError, match="missing required skill metadata"):
        parse_skill_markdown(text)


def test_parse_skill_markdown_rejects_unsupported_inline_lists() -> None:
    text = """---
skill_id: broken
name: Broken
version: 1
scope: shared
source_inspiration: [https://github.com/anthropics/financial-services]
required_outputs:
  - output
max_prompt_chars: 1200
---
# Broken
"""

    with pytest.raises(JueSkillValidationError, match="unsupported frontmatter value"):
        parse_skill_markdown(text)


def test_registry_loads_skills_workflows_and_contracts(tmp_path: Path) -> None:
    root = tmp_path / "jue"
    (root / "skills").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "contracts").mkdir()
    (root / "skills" / "block_design.md").write_text(
        """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Produce executable blocks.
""",
        encoding="utf-8",
    )
    (root / "contracts" / "block_action_contract.json").write_text(
        '{"contract_id":"block_action_contract","version":1,"required":["symbol","target_price","stop_price"],"reject_when":["missing_symbol"]}',
        encoding="utf-8",
    )
    (root / "workflows" / "kis_intraday_manager.json").write_text(
        """{
          "workflow_id": "kis_intraday_manager",
          "version": 1,
          "scope": "kis",
          "model_policy": {
            "default_model": "settings.llm_model",
            "default_reasoning_effort": "settings.llm_reasoning_effort",
            "expected_runtime_model": "gpt-5.5",
            "expected_reasoning_effort": "xhigh"
          },
          "cadence": {"kind": "market_hours", "interval_sec": 1800},
          "required_skills": ["block_design"],
          "required_context": ["account", "blocks"],
          "output_contracts": ["block_action_contract"],
          "authority": {"can_read_untrusted_research": false, "can_write_memory": true, "can_create_blocks": true, "can_submit_orders": false},
          "safety_gates": ["kill_switch", "executable_price_structure"],
          "prompt_budget": {"max_skill_chars": 2000, "max_workflow_chars": 1200, "max_contract_chars": 1200}
        }""",
        encoding="utf-8",
    )

    registry = JueSkillRegistry(root=root)
    pack = registry.compile_prompt_pack("kis_intraday_manager")

    assert pack["workflow_id"] == "kis_intraday_manager"
    assert pack["skills"][0]["skill_id"] == "block_design"
    assert pack["contracts"][0]["contract_id"] == "block_action_contract"
    assert pack["authority"]["can_submit_orders"] is False
    assert "executable_price_structure" in pack["safety_gates"]


def test_default_registry_root_is_package_relative(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    registry = JueSkillRegistry()
    pack = registry.compile_prompt_pack("kis_intraday_manager")

    assert pack["workflow_id"] == "kis_intraday_manager"
    assert pack["skills"]
    assert not str(registry.root).startswith(os.getcwd())


def test_registry_loads_financial_services_source_manifest() -> None:
    registry = JueSkillRegistry(root=Path("src/tradecraft/jue"))

    manifest = registry.load_source_manifest("financial_services")

    assert manifest["source_id"] == "financial_services"
    assert manifest["repository_url"] == "https://github.com/anthropics/financial-services"
    assert any(
        row["local_skill_id"] == "idea_generation"
        and row["source_vertical"] == "equity-research"
        and row["source_skill"] == "idea-generation"
        for row in manifest["mappings"]
    )


def test_prompt_pack_includes_source_manifest_links() -> None:
    registry = JueSkillRegistry(root=Path("src/tradecraft/jue"))

    pack = registry.compile_prompt_pack("kis_intraday_manager")

    assert "source_manifest_links" in pack
    assert any(
        row["source_id"] == "financial_services"
        and row["local_skill_id"] in {"thesis_tracker", "portfolio_balance"}
        for row in pack["source_manifest_links"]
    )


def test_prompt_pack_includes_english_internal_korean_display_language_policy() -> None:
    registry = JueSkillRegistry(root=Path("src/tradecraft/jue"))

    pack = registry.compile_prompt_pack("kis_intraday_manager")

    policy = pack["language_policy"]
    assert policy["internal_reasoning_language"] == "en-US"
    assert policy["operator_display_language"] == "ko-KR"
    assert policy["user_visible_generation_order"] == (
        "draft_conclusion_in_english_then_translate_to_korean_for_display"
    )
    assert "analysis" in policy["english_only_internal_fields"]
    assert "message_md" in policy["translate_for_display_fields"]


def test_contract_compaction_respects_budget(tmp_path: Path) -> None:
    root = tmp_path / "jue"
    (root / "skills").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "contracts").mkdir()
    (root / "skills" / "block_design.md").write_text(
        """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Produce executable blocks.
""",
        encoding="utf-8",
    )
    reject_when = ",".join(f'"reject_{index:02d}"' for index in range(40))
    (root / "contracts" / "block_action_contract.json").write_text(
        f'{{"contract_id":"block_action_contract","version":1,"required":["symbol","target_price","stop_price"],"reject_when":[{reject_when}]}}',
        encoding="utf-8",
    )
    (root / "workflows" / "kis_intraday_manager.json").write_text(
        """{
          "workflow_id": "kis_intraday_manager",
          "version": 1,
          "scope": "kis",
          "model_policy": {
            "default_model": "settings.llm_model",
            "default_reasoning_effort": "settings.llm_reasoning_effort",
            "expected_runtime_model": "gpt-5.5",
            "expected_reasoning_effort": "xhigh"
          },
          "cadence": {"kind": "market_hours", "interval_sec": 1800},
          "required_skills": ["block_design"],
          "required_context": ["account", "blocks"],
          "output_contracts": ["block_action_contract"],
          "authority": {"can_read_untrusted_research": false, "can_write_memory": true, "can_create_blocks": true, "can_submit_orders": false},
          "safety_gates": ["kill_switch", "executable_price_structure"],
          "prompt_budget": {"max_skill_chars": 2000, "max_workflow_chars": 1200, "max_contract_chars": 96}
        }""",
        encoding="utf-8",
    )

    pack = JueSkillRegistry(root=root).compile_prompt_pack("kis_intraday_manager")

    assert pack["prompt_budget"]["used_contract_chars"] <= 96
    assert pack["contracts"][0]["truncated"] is True


def test_contract_compaction_counts_list_brackets(tmp_path: Path) -> None:
    root = tmp_path / "jue"
    (root / "skills").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "contracts").mkdir()
    (root / "skills" / "block_design.md").write_text(
        """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Produce executable blocks.
""",
        encoding="utf-8",
    )
    (root / "contracts" / "block_action_contract.json").write_text(
        '{"contract_id":"block_action_contract","version":1,"required":["symbol","target_price","stop_price"],"reject_when":["missing_symbol","missing_target"]}',
        encoding="utf-8",
    )
    (root / "workflows" / "kis_intraday_manager.json").write_text(
        """{
          "workflow_id": "kis_intraday_manager",
          "version": 1,
          "scope": "kis",
          "model_policy": {
            "default_model": "settings.llm_model",
            "default_reasoning_effort": "settings.llm_reasoning_effort",
            "expected_runtime_model": "gpt-5.5",
            "expected_reasoning_effort": "xhigh"
          },
          "cadence": {"kind": "market_hours", "interval_sec": 1800},
          "required_skills": ["block_design"],
          "required_context": ["account", "blocks"],
          "output_contracts": ["block_action_contract"],
          "authority": {"can_read_untrusted_research": false, "can_write_memory": true, "can_create_blocks": true, "can_submit_orders": false},
          "safety_gates": ["kill_switch", "executable_price_structure"],
          "prompt_budget": {"max_skill_chars": 2000, "max_workflow_chars": 1200, "max_contract_chars": 2}
        }""",
        encoding="utf-8",
    )

    pack = JueSkillRegistry(root=root).compile_prompt_pack("kis_intraday_manager")

    assert pack["contracts"] == []
    assert pack["prompt_budget"]["used_contract_chars"] == 2


def test_contract_compaction_minimum_budget_is_empty_list_size(tmp_path: Path) -> None:
    root = tmp_path / "jue"
    (root / "skills").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "contracts").mkdir()
    (root / "skills" / "block_design.md").write_text(
        """---
skill_id: block_design
name: Block Design
version: 1
scope: shared
source_inspiration:
  - https://github.com/anthropics/financial-services
required_outputs:
  - executable_block
max_prompt_chars: 1200
---
# Block Design

Produce executable blocks.
""",
        encoding="utf-8",
    )
    (root / "contracts" / "block_action_contract.json").write_text(
        '{"contract_id":"block_action_contract","version":1,"required":["symbol"],"reject_when":["missing_symbol"]}',
        encoding="utf-8",
    )
    (root / "workflows" / "kis_intraday_manager.json").write_text(
        """{
          "workflow_id": "kis_intraday_manager",
          "version": 1,
          "scope": "kis",
          "model_policy": {
            "default_model": "settings.llm_model",
            "default_reasoning_effort": "settings.llm_reasoning_effort",
            "expected_runtime_model": "gpt-5.5",
            "expected_reasoning_effort": "xhigh"
          },
          "cadence": {"kind": "market_hours", "interval_sec": 1800},
          "required_skills": ["block_design"],
          "required_context": ["account", "blocks"],
          "output_contracts": ["block_action_contract"],
          "authority": {"can_read_untrusted_research": false, "can_write_memory": true, "can_create_blocks": true, "can_submit_orders": false},
          "safety_gates": ["kill_switch", "executable_price_structure"],
          "prompt_budget": {"max_skill_chars": 2000, "max_workflow_chars": 1200, "max_contract_chars": 1}
        }""",
        encoding="utf-8",
    )

    pack = JueSkillRegistry(root=root).compile_prompt_pack("kis_intraday_manager")

    assert pack["contracts"] == []
    assert pack["prompt_budget"]["max_contract_chars"] == 1
    assert pack["prompt_budget"]["used_contract_chars"] == 2


def test_registry_wraps_json_and_integer_errors(tmp_path: Path) -> None:
    root = tmp_path / "jue"
    (root / "skills").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "contracts").mkdir()
    (root / "contracts" / "broken_contract.json").write_text("{", encoding="utf-8")
    registry = JueSkillRegistry(root=root)

    with pytest.raises(JueSkillValidationError, match="contract broken_contract json malformed"):
        registry.load_contract("broken_contract")

    (root / "contracts" / "bad_version.json").write_text(
        '{"contract_id":"bad_version","version":"abc","required":["symbol"],"reject_when":["missing"]}',
        encoding="utf-8",
    )

    with pytest.raises(JueSkillValidationError, match="contract bad_version version"):
        registry.load_contract("bad_version")
