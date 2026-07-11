from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tradecraft.services.jue_skill_registry import JueSkillRegistry


JUE_ROOT = Path("src/tradecraft/jue")
REQUIRED_SKILLS = {
    "idea_generation",
    "thesis_tracker",
    "catalyst_calendar",
    "block_design",
    "risk_sizing",
    "execution_review",
    "portfolio_balance",
    "crypto_market_sweep",
    "evidence_audit",
    "policy_revision",
    "earnings_preview",
    "earnings_analysis",
    "sector_overview",
    "model_update",
    "morning_note",
    "valuation_frame",
}
REQUIRED_WORKFLOWS = {
    "kis_pre_open",
    "kis_intraday_manager",
    "kis_post_close",
    "binance_cycle",
    "crypto_research",
    "instant_symbol_analysis",
    "block_reflection",
    "policy_revision",
    "kis_morning_note",
    "kis_idea_screen",
    "kis_symbol_deep_dive",
    "kis_earnings_update",
    "kis_sector_rotation",
    "portfolio_rebalance",
}


def test_required_jue_skills_exist_and_parse() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    for skill_id in sorted(REQUIRED_SKILLS):
        skill = registry.load_skill(skill_id)
        assert skill["skill_id"] == skill_id
        assert skill["version"] >= 1
        assert skill["content_md"].strip()
        assert skill["source_inspiration"]
        assert skill["required_outputs"]


def test_jue_skills_do_not_reintroduce_old_disclaimer_identity() -> None:
    forbidden = [
        "매매 추천 아님",
        "정보 제공용",
        "financial advice",
        "not recommendation",
        "not recommendations",
    ]
    for path in sorted((JUE_ROOT / "skills").glob("*.md")):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase.lower() not in text, f"{path} contains forbidden phrase: {phrase}"


def test_required_workflows_compile() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    for workflow_id in sorted(REQUIRED_WORKFLOWS):
        pack = registry.compile_prompt_pack(workflow_id)
        assert pack["workflow_id"] == workflow_id
        assert pack["skills"]
        assert pack["contracts"]
        assert pack["safety_gates"]
        assert pack["prompt_budget"]["used_skill_chars"] <= pack["prompt_budget"]["max_skill_chars"]


def test_workflow_model_policy_matches_jue_split() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    kis = registry.compile_prompt_pack("kis_intraday_manager")
    binance = registry.compile_prompt_pack("binance_cycle")

    assert kis["model_policy"]["expected_runtime_model"] == "gpt-5.6-sol"
    assert kis["model_policy"]["expected_reasoning_effort"] == "xhigh"
    assert binance["model_policy"]["expected_runtime_model"] == "gpt-5.6-sol"
    assert binance["model_policy"]["expected_reasoning_effort"] == "xhigh"


def test_contracts_have_required_and_rejection_rules() -> None:
    for path in sorted((JUE_ROOT / "contracts").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["contract_id"] == path.stem
        assert data["version"] >= 1
        assert data["required"]
        assert data["reject_when"]


def test_financial_services_manifest_mappings_point_to_local_skills() -> None:
    registry = JueSkillRegistry(root=JUE_ROOT)

    manifest = registry.load_source_manifest("financial_services")

    assert manifest["mappings"]
    for row in manifest["mappings"]:
        skill = registry.load_skill(row["local_skill_id"])
        assert skill["skill_id"] == row["local_skill_id"]


def test_check_jue_workflows_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_jue_workflows.py"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Jue workflow check OK" in result.stdout


def test_jue_assets_are_included_as_package_data() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.package-data]" in text
    assert '"jue/skills/*.md"' in text
    assert '"jue/workflows/*.json"' in text
    assert '"jue/contracts/*.json"' in text
    assert '"jue/sources/*.json"' in text
