from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_config_spec_captured_settings_count_matches_app_settings() -> None:
    from scripts.check_project_contracts import (
        docs_config_settings_count,
        settings_model_field_count,
    )

    assert docs_config_settings_count(ROOT / "docs/spec/12_config_env.md") == (
        settings_model_field_count()
    )


def test_repository_has_no_tracked_generated_package_metadata() -> None:
    from scripts.check_project_contracts import tracked_generated_files

    assert tracked_generated_files(ROOT) == []


def _doc(name: str) -> str:
    return (ROOT / "docs" / "spec" / name).read_text(encoding="utf-8")


def _project_script_names() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        block = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    except IndexError:
        return set()
    names: set[str] = set()
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.add(line.split("=", 1)[0].strip())
    return names


def _api_route_inventory_rows() -> list[tuple[str, str, str]]:
    text = (ROOT / "docs/spec/appendix/api_route_inventory.md").read_text(
        encoding="utf-8"
    )
    block = text.split("```text", 1)[1].split("```", 1)[0]
    rows: list[tuple[str, str, str]] = []
    for raw_line in block.splitlines():
        if not raw_line.strip():
            continue
        methods = raw_line[:21].strip()
        rest = raw_line[21:].strip()
        parts = rest.split()
        if not parts:
            continue
        rows.append((methods, parts[0], parts[-1] if len(parts) > 1 else ""))
    return rows


def _current_api_route_rows() -> list[tuple[str, str, str]]:
    from tradecraft.main import app

    rows = []
    for route in app.routes:
        methods = ",".join(sorted(getattr(route, "methods", []) or []))
        path = getattr(route, "path", "")
        name = getattr(route, "name", "")
        rows.append((methods, path, name))
    return sorted(rows, key=lambda item: (item[1], item[0], item[2]))


def test_spec_documents_jue_workflow_registry() -> None:
    llm_system = _doc("05_llm_system.md")
    research_memory = _doc("08_research_memory.md")
    roadmap = _doc("16_refactor_roadmap.md")

    assert "Jue Workflow Registry" in llm_system
    assert "GET /api/jue/workflows/status" in llm_system
    assert "Decision Lifecycle Artifacts" in llm_system
    assert "GET /api/jue/source-manifest" in llm_system
    assert "decision_lifecycle_v3" in llm_system
    assert "Workflow-Guided Memory" in research_memory
    assert "Lifecycle Artifacts" in research_memory
    assert "jue_lifecycle_artifacts" in research_memory
    assert "InvestmentMemoryService.context_pack()" in research_memory
    assert "`src/tradecraft/jue` workflow packs" in roadmap


def test_api_route_inventory_matches_current_app_routes() -> None:
    assert _api_route_inventory_rows() == _current_api_route_rows()


def test_specs_document_llm_status_and_probe_routes() -> None:
    api_reference = _doc("11_api_reference.md")
    observability = _doc("14_observability.md")
    runbook = _doc("23_operations_runbook.md")

    for content in (api_reference, observability, runbook):
        assert "/api/llm/status" in content
        assert "/api/llm/probe" in content


def test_specs_document_jue_wiki_layer() -> None:
    required = {
        "docs/spec/08_research_memory.md": ["Jue Wiki", "compiled interpretation layer", "RAG"],
        "docs/spec/21_memory_learning_contracts.md": ["Jue Wiki", "context_pack", "source-of-truth"],
        "docs/spec/03_runtime_processes.md": ["tradecraft-jue-wiki"],
        "docs/spec/04_databases.md": [".runtime/jue_wiki/wiki.db"],
        "docs/spec/11_api_reference.md": [
            "/api/wiki/status",
            "/api/wiki/context",
            "/api/wiki/pages/{page_id}",
            "/api/wiki/rebuild",
            "/api/wiki/lint",
        ],
        "docs/spec/12_config_env.md": [
            "jue_wiki_enabled",
            "jue_wiki_root_path",
            "jue_wiki_db_path",
            "jue_wiki_context_max_chars",
            "jue_wiki_runner_interval_sec",
            "jue_wiki_page_max_chars",
            "jue_wiki_context_page_limit",
        ],
        ".env.example": [
            "TRADECRAFT_JUE_WIKI_ENABLED",
            "TRADECRAFT_JUE_WIKI_ROOT_PATH",
            "TRADECRAFT_JUE_WIKI_DB_PATH",
            "TRADECRAFT_JUE_WIKI_CONTEXT_MAX_CHARS",
            "TRADECRAFT_JUE_WIKI_PAGE_MAX_CHARS",
            "TRADECRAFT_JUE_WIKI_CONTEXT_PAGE_LIMIT",
            "TRADECRAFT_JUE_WIKI_RUNNER_INTERVAL_SEC",
        ],
    }
    for path, needles in required.items():
        content = (ROOT / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in content


def test_specs_document_jue_wiki_phase2() -> None:
    research = Path("docs/spec/08_research_memory.md").read_text()
    memory = Path("docs/spec/21_memory_learning_contracts.md").read_text()
    api = Path("docs/spec/11_api_reference.md").read_text()
    env = Path("docs/spec/12_config_env.md").read_text()

    assert "Jue Wiki Selector" in research
    assert "wiki_selection_runs" in memory
    assert "/api/wiki/search" in api
    assert "TRADECRAFT_JUE_WIKI_PROMPT_MODE" in env


def test_specs_document_jue_wiki_phase3() -> None:
    research = Path("docs/spec/08_research_memory.md").read_text(encoding="utf-8")
    memory = Path("docs/spec/21_memory_learning_contracts.md").read_text(
        encoding="utf-8"
    )
    api = Path("docs/spec/11_api_reference.md").read_text(encoding="utf-8")
    env = Path("docs/spec/12_config_env.md").read_text(encoding="utf-8")

    assert "Applied Intelligence Loop" in research
    assert "wiki_decision_links" in memory
    assert "/api/wiki/application/effectiveness" in api
    assert "TRADECRAFT_JUE_WIKI_EFFECTIVENESS_WEIGHT" in env


def test_active_runtime_docs_do_not_expose_retired_kis_llm_runner() -> None:
    checked_paths = [
        "pyproject.toml",
        "README.md",
        "AGENTS.md",
        "docs/spec/01_current_inventory.md",
        "docs/spec/03_runtime_processes.md",
        "docs/spec/17_refactor_reference.md",
        "docs/spec/23_operations_runbook.md",
    ]
    for path in checked_paths:
        content = (ROOT / path).read_text(encoding="utf-8")
        assert "tradecraft-kis-trader" not in content, path

    runtime_processes = (ROOT / "docs/spec/03_runtime_processes.md").read_text(
        encoding="utf-8"
    )
    readiness_section = runtime_processes.split("## Readiness Checks", 1)[1]
    assert "`kis_block_trader`" in readiness_section
    assert "`kis_trader`" not in readiness_section


def test_current_inventory_avoids_ephemeral_runtime_artifacts() -> None:
    content = (ROOT / "docs/spec/01_current_inventory.md").read_text(
        encoding="utf-8"
    )

    assert ".runtime/test_" not in content
    assert ".runtime/tmp_" not in content
    assert "| PID File | Captured PID |" not in content
    assert "Runtime inventory is intentionally not pinned to PID values." in content


def test_active_api_docs_do_not_expose_retired_kis_trader_routes() -> None:
    checked_paths = [
        "docs/spec/11_api_reference.md",
        "docs/spec/14_observability.md",
        "docs/spec/appendix/api_route_inventory.md",
    ]
    for path in checked_paths:
        content = (ROOT / path).read_text(encoding="utf-8")
        assert "/api/kis/trader" not in content, path

    api_reference = (ROOT / "docs/spec/11_api_reference.md").read_text(
        encoding="utf-8"
    )
    inventory = (
        ROOT / "docs/spec/appendix/api_route_inventory.md"
    ).read_text(encoding="utf-8")
    assert "/api/rebalance/kis-status" in api_reference
    assert "/api/rebalance/kis-status" in inventory


def test_readme_describes_runtime_as_state_writer_not_order_engine() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "## Runtime State Writer" in readme
    assert "state_writer_no_orders" in readme
    assert "주문 실행 권한 없음" in readme
    assert "safe_default_no_orders" in readme
    assert "## Runtime Skeleton" not in readme
    assert "기본 세션으로 자동 fallback" not in readme


def test_readme_documents_current_local_control_url() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "http://127.0.0.1:18080" in readme
    assert "브라우저에서 [http://127.0.0.1:8000]" not in readme


def test_readme_documents_primary_jue_runtime_entrypoints() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for command in [
        "tradecraft-control",
        "tradecraft-runtime",
        "tradecraft-naver-reports",
        "tradecraft-strategy-insights",
        "tradecraft-market-pulse",
        "tradecraft-market-judge",
        "tradecraft-investment-memory",
        "tradecraft-live-evaluator",
        "tradecraft-jue-wiki",
        "tradecraft-kis-block-trader",
        "tradecraft-binance-block-trader",
        "tradecraft-crypto-market-research",
        "tradecraft-crypto-pattern-lab",
        "tradecraft-crypto-alpha",
        "tradecraft-watchdog",
    ]:
        assert command in readme


def test_readme_tradecraft_commands_are_installed_console_scripts() -> None:
    import re

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    scripts = _project_script_names()
    commands: set[str] = set()
    for block in re.findall(r"```bash\n(.*?)```", readme, flags=re.S):
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if line.startswith("tradecraft-"):
                commands.add(line.split()[0])

    assert commands
    assert commands <= scripts


def test_runtime_docs_match_report_collection_ownership() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runtime_processes = (ROOT / "docs/spec/03_runtime_processes.md").read_text(
        encoding="utf-8"
    )

    assert "리포트 수집까지 함께 돌릴 때는" not in readme
    assert "리포트 수집 + RAG sync + KRX 리서치/조언 통합 러너" not in readme
    assert "리포트 수집은 기본적으로 `tradecraft-naver-reports`가 담당합니다." in readme
    assert "Primary Naver report crawler and RAG sync runner" in runtime_processes
    assert "report collection is intentionally disabled here" in runtime_processes
    assert "TRADECRAFT_RESEARCH_RUNNER_COLLECT_REPORTS=true" in runtime_processes


def test_runtime_docs_match_binance_manager_default_cadence() -> None:
    runtime_processes = (ROOT / "docs/spec/03_runtime_processes.md").read_text(
        encoding="utf-8"
    )

    assert "active default manager cadence is 10 minutes" not in runtime_processes
    assert "active default manager cadence is 30 minutes (`1800` seconds)" in runtime_processes
    assert "deterministic executor ticks remain faster" in runtime_processes
