from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tradecraft.runtime import jue_codex_lab_runner
from tradecraft.services.jue_codex_lab import JueCodexLabService
from tradecraft.runtime.jue_codex_lab_runner import (
    run_jue_codex_lab_cycle,
    run_jue_codex_lab_cycle_isolated,
)


class FakeLab:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def ingest_validation_work_queue(self, *, venue: str) -> dict[str, object]:
        self.calls.append(("ingest", venue))
        return {"status": "ok", "venue": venue, "created_count": 1}

    def run_once(self, *, max_tasks: int) -> dict[str, object]:
        self.calls.append(("run_once", max_tasks))
        return {"status": "ok", "repaired_count": max_tasks}


class HangingLab(FakeLab):
    def run_once(self, *, max_tasks: int) -> dict[str, object]:
        self.calls.append(("run_once", max_tasks))
        time.sleep(10)
        return {"status": "ok", "repaired_count": max_tasks}


def test_run_jue_codex_lab_cycle_ingests_both_venues_before_repairs() -> None:
    lab = FakeLab()

    result = run_jue_codex_lab_cycle(lab=lab, max_tasks=2)

    assert lab.calls == [
        ("ingest", "kis"),
        ("ingest", "binance"),
        ("run_once", 2),
    ]
    assert result == {
        "status": "ok",
        "ingest": {
            "kis": {"status": "ok", "venue": "kis", "created_count": 1},
            "binance": {"status": "ok", "venue": "binance", "created_count": 1},
        },
        "repair": {"status": "ok", "repaired_count": 2},
    }


def test_run_jue_codex_lab_cycle_isolated_times_out_hanging_repair() -> None:
    result = run_jue_codex_lab_cycle_isolated(
        lab=HangingLab(),
        max_tasks=1,
        timeout_sec=0.1,
    )

    assert result["status"] == "error"
    assert result["repair"]["status"] == "error"
    assert result["repair"]["errors"][0]["reason"] == "cycle_timeout"
    assert result["repair"]["failed_count"] == 1


def test_run_is_retired_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []

    def failing_app_settings() -> object:
        events.append(("settings", "start"))
        raise RuntimeError("settings failed")

    monkeypatch.setattr(jue_codex_lab_runner, "AppSettings", failing_app_settings)

    jue_codex_lab_runner.run()

    assert events == []


def test_build_lab_wires_codex_runtime_and_safety_settings(tmp_path) -> None:
    settings = SimpleNamespace(
        jue_codex_lab_db_path=str(tmp_path / "codex_lab.db"),
        trading_validation_db_path=str(tmp_path / "validation.db"),
        codex_runtime_mode="sdk",
        codex_runtime_sdk_codex_bin="codex",
        codex_runtime_timeout_ms=600000,
        llm_model="gpt-5.5",
        llm_reasoning_effort="xhigh",
        llm_usage_enabled=True,
        llm_usage_db_path=str(tmp_path / "llm_usage.db"),
        codex_native_thread_mode="daily",
        codex_native_thread_db_path=str(tmp_path / "threads.db"),
        codex_native_compact_after_turns=8,
        codex_native_read_turns=4,
        codex_native_developer_instructions_enabled=True,
        jue_codex_lab_autonomy_mode="proposal_only",
        jue_codex_lab_max_patch_bytes=777,
        jue_codex_lab_allowed_paths="src/tradecraft,tests",
        jue_codex_lab_blocked_paths=".env,.runtime",
        jue_codex_lab_market_hours_hot_deploy=False,
    )

    lab = jue_codex_lab_runner._build_lab(settings)

    assert isinstance(lab, JueCodexLabService)
    assert lab.codex_runtime is not None
    assert lab.codex_runtime.config.usage_component == "jue_codex_lab"
    assert lab.autonomy_mode == "proposal_only"
    assert lab.max_patch_bytes == 777
    assert lab.allowed_paths == ["src/tradecraft", "tests"]
    assert lab.blocked_paths == [".env", ".runtime"]
    assert lab.market_hours_hot_deploy is False
