from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_verify_module() -> ModuleType:
    path = ROOT / "scripts" / "verify.py"
    spec = importlib.util.spec_from_file_location("tradecraft_verify", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fast_verification_selects_only_unit_and_contract_tests() -> None:
    verify = _load_verify_module()

    steps = verify.build_steps("fast", xdist_available=True)
    pytest_command = steps[-1].command

    assert steps[0].name == "project-contracts"
    assert steps[1].name == "ruff"
    assert "(unit or contract) and not slow" in pytest_command
    assert pytest_command[pytest_command.index("-n") + 1] == "auto"
    assert "--durations=50" in pytest_command


def test_domain_verification_requires_and_selects_supported_area() -> None:
    verify = _load_verify_module()

    steps = verify.build_steps("domain", area="binance", xdist_available=True)
    pytest_command = steps[-1].command

    assert "binance" in pytest_command
    assert "-n" not in pytest_command
    with pytest.raises(ValueError, match="--area"):
        verify.build_steps("domain")
    with pytest.raises(ValueError, match="unsupported area"):
        verify.build_steps("domain", area="unknown")


def test_full_verification_runs_entire_suite_without_marker_filter() -> None:
    verify = _load_verify_module()

    steps = verify.build_steps("full", xdist_available=False)
    pytest_command = steps[-1].command

    assert pytest_command[-1] == "tests"
    assert pytest_command.count("-m") == 1
    assert verify.PROFILE_BUDGET_SECONDS["full"] == 15 * 60
