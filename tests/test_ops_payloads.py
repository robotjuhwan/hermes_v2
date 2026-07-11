from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import tradecraft.api.ops_payloads as ops_payloads
import tradecraft.api.ops_readiness as ops_readiness
from tradecraft.api.ops_payloads import (
    append_trading_validation_ops_signals,
    build_ops_binance_block_trader_payload,
    build_ops_crypto_alpha_payload,
    build_ops_crypto_market_research_payload,
    build_ops_environment_signals,
    build_ops_jue_wiki_payload,
    build_ops_kis_block_trader_payload,
    build_ops_live_evaluator_payload,
    build_ops_market_judge_payload,
    build_ops_market_pulse_payload,
    build_ops_memory_payload,
    build_ops_reports_payload,
    build_ops_remediation_actions,
    build_ops_runner_liveness,
    build_ops_trading_validation_payload,
    build_ops_watchdog_payload,
    finalize_ops_readiness_signals,
    merge_section_readiness_signals,
)

ROOT = Path(__file__).resolve().parents[1]


def test_llm_operational_status_lives_in_ops_payloads_not_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    ops_source = (ROOT / "src/tradecraft/api/ops_payloads.py").read_text()

    assert "def _build_llm_operational_status(" not in main_source
    assert "def build_llm_operational_status(" in ops_source


def test_core_runner_processes_live_in_ops_readiness_not_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    ops_source = (ROOT / "src/tradecraft/api/ops_readiness.py").read_text()

    assert "process_map = {" not in main_source
    assert "def build_core_runner_processes(" in ops_source
    assert "process_map = {" in ops_source


def test_ops_readiness_reports_section_uses_naver_reports_runner_key() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()

    assert 'runner=processes.get("naver_reports", {})' in main_source
    assert 'runner=processes.get("research", {})' not in main_source


def test_runner_cover_helpers_live_in_ops_readiness_not_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    ops_source = (ROOT / "src/tradecraft/api/ops_readiness.py").read_text()

    assert "def runner_status_with_cover(" in ops_source
    assert "def light_runner_process_status(" in ops_source
    assert 'payload["covered_by_label"]' not in main_source
    assert "include_matches=False" not in main_source


def test_market_readiness_helpers_live_in_ops_readiness_not_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    ops_source = (ROOT / "src/tradecraft/api/ops_readiness.py").read_text()

    assert "def _market_judgment_readiness_status(" not in main_source
    assert "def _market_pulse_readiness_status(" not in main_source
    assert "def build_market_judgment_readiness_status(" in ops_source
    assert "def build_market_pulse_readiness_status(" in ops_source


def test_build_market_judgment_readiness_status_normalizes_latest_run() -> None:
    class Repository:
        path = "/tmp/market_judgment.db"

        def recent_runs(self, *, limit: int) -> list[dict]:
            assert limit == 1
            return [
                {
                    "run_at": "2026-06-21T09:00:00+09:00",
                    "status": "ok",
                    "mode": "regular",
                }
            ]

    class Config:
        quote_interval_sec = "60"
        judge_interval_sec = "1800"
        max_symbols = "30"
        llm_max_symbols = "12"
        use_naver_fallback = False

    class Engine:
        repository = Repository()
        config = Config()

    payload = ops_readiness.build_market_judgment_readiness_status(Engine())

    assert payload == {
        "status": "ok",
        "db_path": "/tmp/market_judgment.db",
        "latest_run_at": "2026-06-21T09:00:00+09:00",
        "latest_run_status": "ok",
        "latest_run_mode": "regular",
        "config": {
            "quote_interval_sec": 60,
            "judge_interval_sec": 1800,
            "max_symbols": 30,
            "llm_max_symbols": 12,
            "use_naver_fallback": False,
        },
    }


def test_build_market_pulse_readiness_status_handles_missing_latest() -> None:
    class Repository:
        path = "/tmp/market_pulse.db"

        def latest(self) -> dict:
            return {"status": "missing", "captured_at": ""}

    class Config:
        enabled = False

    class Service:
        repository = Repository()
        config = Config()

    payload = ops_readiness.build_market_pulse_readiness_status(Service())

    assert payload == {
        "status": "ok",
        "db_path": "/tmp/market_pulse.db",
        "latest": {},
        "enabled": False,
    }


def test_ops_readiness_payload_schema_lives_in_ops_readiness_not_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    ops_source = (ROOT / "src/tradecraft/api/ops_readiness.py").read_text()

    assert "def build_ops_readiness_payload(" in ops_source
    assert '"checked_at": datetime.now' not in main_source
    assert '"next_market_open_at": str(' not in main_source


def test_build_ops_readiness_payload_assembles_canonical_sections() -> None:
    payload = ops_readiness.build_ops_readiness_payload(
        readiness_signals={
            "status": "yellow",
            "blockers": ["admin_token_not_configured"],
            "warnings": ["memory_not_seeded"],
            "advisory_details": [
                {
                    "signal": "trading_validation_probe_kis",
                    "venue": "kis",
                    "readiness": "probe",
                }
            ],
            "stale_processes": ["control"],
            "missing_processes": ["watchdog"],
            "remediation_actions": [{"id": "restart_control"}],
        },
        checked_at="2026-06-21T00:00:00+00:00",
        processes={"control": {"alive": True}},
        admin_token_configured=False,
        kis_ready=True,
        kis_rate_limit={"enabled": True},
        llm_ready=True,
        disk_space={"status": "ok"},
        llm={"configured": True},
        llm_usage={"today": {"total_tokens": 123}},
        semantic_checks={"status": "ok"},
        codex_native={"status": "ok"},
        telegram_ready=True,
        live_trading_enabled=True,
        paper_mode=False,
        kill_switch={"enabled": False},
        sections={
            "memory": {"enabled": True},
            "reports": {"enabled": True},
            "live_evaluator": {"enabled": True},
            "trading_validation": {"status": "ok"},
            "watchdog": {"enabled": True},
            "market_judge": {"enabled": True},
            "market_pulse": {"enabled": True},
            "kis_block_trader": {"enabled": True},
            "binance_block_trader": {"enabled": True},
            "crypto_market_research": {"enabled": True},
            "crypto_alpha": {"enabled": True},
        },
        next_market_open_at="2026-06-22T00:00:00+09:00",
    )

    assert payload["status"] == "yellow"
    assert payload["checked_at"] == "2026-06-21T00:00:00+00:00"
    assert payload["blockers"] == ["admin_token_not_configured"]
    assert payload["warnings"] == ["memory_not_seeded"]
    assert payload["advisory_details"] == [
        {
            "signal": "trading_validation_probe_kis",
            "venue": "kis",
            "readiness": "probe",
        }
    ]
    assert payload["processes"] == {"control": {"alive": True}}
    assert payload["admin_token_configured"] is False
    assert payload["kis_ready"] is True
    assert payload["live_trading_enabled"] is True
    assert payload["paper_mode"] is False
    assert payload["memory"] == {"enabled": True}
    assert payload["binance_block_trader"] == {"enabled": True}
    assert payload["next_market_open_at"] == "2026-06-22T00:00:00+09:00"


def test_build_ops_readiness_payload_compacts_process_rows() -> None:
    payload = ops_readiness.build_ops_readiness_payload(
        readiness_signals={"status": "green"},
        checked_at="2026-06-21T00:00:00+00:00",
        processes={
            "control": {
                "key": "control",
                "label": "control API",
                "status": "running",
                "alive": True,
                "direct_alive": True,
                "effective_alive": True,
                "pid": 123,
                "started_at": "2026-06-21T09:00:00+09:00",
                "started_at_epoch": 1_782_000_000.0,
                "pid_file": ".runtime/pids/control.pid",
                "pid_file_pid": 123,
                "pid_file_status": "ok",
                "matched_count": 1,
                "matches": [
                    {
                        "pid": 123,
                        "command": "/Users/juhwan/hermes_v2/.venv/bin/python "
                        + "x" * 20_000,
                    }
                ],
                "checked_paths": ["runtime/runner.py", "services/foo.py"],
                "code_mtime": "2026-06-21T00:00:00+00:00",
                "code_mtime_epoch": 1_782_000_001.0,
                "stale_process": False,
            }
        },
        admin_token_configured=True,
        kis_ready=True,
        kis_rate_limit={},
        llm_ready=True,
        disk_space={},
        llm={},
        llm_usage={},
        semantic_checks={},
        codex_native={},
        telegram_ready=True,
        live_trading_enabled=True,
        paper_mode=False,
        kill_switch={"enabled": False},
        sections={},
        next_market_open_at="",
    )

    assert payload["processes"]["control"] == {
        "key": "control",
        "label": "control API",
        "status": "running",
        "alive": True,
        "pid": 123,
        "started_at": "2026-06-21T09:00:00+09:00",
        "pid_file_pid": 123,
        "pid_file_status": "ok",
        "matched_count": 1,
        "direct_alive": True,
        "effective_alive": True,
        "code_mtime": "2026-06-21T00:00:00+00:00",
        "stale_process": False,
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "matches" not in serialized
    assert "checked_paths" not in serialized
    assert "started_at_epoch" not in serialized
    assert "code_mtime_epoch" not in serialized
    assert len(serialized) < 2_500


def test_build_ops_readiness_payload_compacts_llm_usage_summary() -> None:
    payload = ops_readiness.build_ops_readiness_payload(
        readiness_signals={"status": "green"},
        checked_at="2026-06-21T00:00:00+00:00",
        processes={},
        admin_token_configured=True,
        kis_ready=True,
        kis_rate_limit={},
        llm_ready=True,
        disk_space={},
        llm={},
        llm_usage={
            "enabled": True,
            "db_path": ".runtime/llm_usage.db",
            "today": {
                "status": "ok",
                "period": "today",
                "trading_day": "2026-06-21",
                "total": {
                    "call_count": 10,
                    "error_count": 1,
                    "total_tokens": 1234,
                    "prompt_tokens": 1000,
                    "completion_tokens": 234,
                    "input_chars": 999_999,
                    "output_chars": 99_999,
                },
                "by_component": [
                    {
                        "component": f"component_{index}",
                        "label": f"Component {index}",
                        "category": "research",
                        "description": "x" * 10_000,
                        "call_count": index,
                        "error_count": 0,
                        "total_tokens": 1000 - index,
                        "prompt_tokens": 800 - index,
                        "completion_tokens": 200,
                        "latest_status": "ok",
                        "latest_started_at": "2026-06-21T00:00:00+00:00",
                        "latest_input_chars": 12345,
                        "latest_error_at": "",
                        "raw": "r" * 10_000,
                    }
                    for index in range(10)
                ],
            },
        },
        semantic_checks={},
        codex_native={},
        telegram_ready=True,
        live_trading_enabled=True,
        paper_mode=False,
        kill_switch={"enabled": False},
        sections={},
        next_market_open_at="",
    )

    llm_usage = payload["llm_usage"]
    assert llm_usage["enabled"] is True
    assert llm_usage["today"]["total"]["total_tokens"] == 1234
    assert llm_usage["today"]["total"]["call_count"] == 10
    assert len(llm_usage["today"]["by_component"]) == 6
    assert llm_usage["today"]["by_component"][0] == {
        "component": "component_0",
        "label": "Component 0",
        "category": "research",
        "call_count": 0,
        "error_count": 0,
        "total_tokens": 1000,
        "prompt_tokens": 800,
        "completion_tokens": 200,
        "latest_status": "ok",
        "latest_started_at": "2026-06-21T00:00:00+00:00",
    }
    serialized = json.dumps(llm_usage, ensure_ascii=False)
    assert "description" not in serialized
    assert "latest_input_chars" not in serialized
    assert "raw" not in serialized
    assert len(serialized) < 2_500


def test_build_ops_readiness_payload_compacts_codex_native_status() -> None:
    payload = ops_readiness.build_ops_readiness_payload(
        readiness_signals={"status": "green"},
        checked_at="2026-06-21T00:00:00+00:00",
        processes={},
        admin_token_configured=True,
        kis_ready=True,
        kis_rate_limit={},
        llm_ready=True,
        disk_space={"status": "ok"},
        llm={},
        llm_usage={},
        semantic_checks={},
        codex_native={
            "status": "ok",
            "mode": "sdk",
            "model": "gpt-5.5",
            "reasoning_effort": "xhigh",
            "thread_mode": "daily",
            "latest_account_check": {
                "status": "ok",
                "account_label": "pro",
                "detail": {"raw": "account=email@example.com " + ("x" * 20_000)},
                "checked_at": "2026-06-21T00:00:00+00:00",
            },
            "models": [{"model": "gpt-5.5", "available": True}],
            "components": [{"component": "kis_block_manager", "model": "gpt-5.5"}],
            "last_error": {
                "component": "market_judge",
                "error_message": "boom",
                "raw": "e" * 20_000,
            },
            "last_recovered_error": {
                "component": "binance_block_manager",
                "error_message": "timeout",
                "recovered_at": "2026-06-21T00:05:00+00:00",
                "recovery_reason": "component_service_status_ok",
                "raw": "x" * 20_000,
            },
            "recent_runtime_events": [
                {
                    "component": "market_judge",
                    "event": "error",
                    "error_message": "boom",
                    "raw": "r" * 20_000,
                },
                {
                    "component": "binance_block_manager",
                    "status": "recovered",
                    "error_message": "timeout",
                    "recovered_at": "2026-06-21T00:05:00+00:00",
                    "recovery_reason": "component_service_status_ok",
                    "raw": "r" * 20_000,
                }
            ],
            "recent_turns": [{"prompt": "p" * 20_000, "response": "r" * 20_000}],
        },
        telegram_ready=True,
        live_trading_enabled=True,
        paper_mode=False,
        kill_switch={"enabled": False},
        sections={},
        next_market_open_at="",
    )

    native = payload["codex_native"]
    assert native["status"] == "ok"
    assert native["latest_account_check"] == {
        "status": "ok",
        "account_label": "pro",
        "checked_at": "2026-06-21T00:00:00+00:00",
    }
    assert native["last_error"] == {
        "component": "market_judge",
        "error_message": "boom",
    }
    assert native["last_recovered_error"] == {
        "component": "binance_block_manager",
        "error_message": "timeout",
        "recovered_at": "2026-06-21T00:05:00+00:00",
        "recovery_reason": "component_service_status_ok",
    }
    assert native["recent_runtime_events"][1] == {
        "component": "binance_block_manager",
        "status": "recovered",
        "error_message": "timeout",
        "recovered_at": "2026-06-21T00:05:00+00:00",
        "recovery_reason": "component_service_status_ok",
    }
    serialized = json.dumps(native, ensure_ascii=False)
    assert "recent_turns" not in serialized
    assert "email@example.com" not in serialized
    assert len(serialized) < 3_000


def test_codex_native_model_status_prefers_recent_successful_turn() -> None:
    payload = ops_readiness.build_ops_readiness_payload(
        readiness_signals={"status": "green"},
        checked_at="2026-06-29T00:00:00+00:00",
        processes={},
        admin_token_configured=True,
        kis_ready=True,
        kis_rate_limit={},
        llm_ready=True,
        disk_space={},
        llm={},
        llm_usage={},
        semantic_checks={},
        codex_native={
            "status": "ok",
            "mode": "sdk",
            "model": "gpt-5.5",
            "models": [
                {
                    "model": "gpt-5.5",
                    "available": False,
                    "error_message": "configured model not returned by Codex SDK models()",
                    "checked_at": "2026-06-18T00:00:00+00:00",
                }
            ],
            "recent_turns": [
                {
                    "component": "kis_block_manager",
                    "model": "gpt-5.5",
                    "status": "ok",
                    "finished_at": "2026-06-29T00:00:00+00:00",
                }
            ],
        },
        telegram_ready=True,
        live_trading_enabled=True,
        paper_mode=False,
        kill_switch={"enabled": False},
        sections={},
        next_market_open_at="",
    )

    model = payload["codex_native"]["models"][0]
    assert model["model"] == "gpt-5.5"
    assert model["available"] is True
    assert model["availability_source"] == "recent_successful_turn"
    assert model["last_successful_turn_at"] == "2026-06-29T00:00:00+00:00"
    assert "error_message" not in model


def test_ops_block_trader_payloads_compact_large_status_sections() -> None:
    large_status = {
        "status": "ok",
        "db_path": ".runtime/blocks.db",
        "block_count": 12,
        "open_block_count": 3,
        "manager_run_count": 100,
        "latest_manager_run_at": "2026-06-29T09:00:00+09:00",
        "live_authority": {
            "status": "ok",
            "live_grade": "probe",
            "raw_payload": "x" * 80_000,
            "validation_gate": {
                "status": "probe",
                "operator_guidance": ["y" * 20_000] * 10,
            },
        },
        "open_blocks": [{"metadata": {"huge": "z" * 80_000}}],
        "manager_runs": [{"prompt": {"huge": "p" * 80_000}}],
    }

    kis_payload = build_ops_kis_block_trader_payload(
        enabled=True,
        status=large_status,
        next_manager_run_at="next-kis",
    )
    binance_payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status=large_status,
        runner={"status": "running", "matches": [{"command": "c" * 80_000}]},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        account_risk_pct=1,
        max_total_exposure_usdt=100,
        max_symbol_exposure_pct=20,
        min_reward_risk=1.5,
        next_manager_run_at="next-binance",
    )

    for payload in (kis_payload, binance_payload):
        encoded = json.dumps(payload, ensure_ascii=False)
        assert len(encoded) < 20_000
        assert payload["status"]["block_count"] == 12
        assert payload["status"]["live_authority"]["live_grade"] == "probe"
        assert "raw_payload" not in payload["status"]["live_authority"]
        assert "open_blocks" not in payload["status"]
        assert "manager_runs" not in payload["status"]
    assert "matches" not in binance_payload["runner"]


def test_build_ops_trading_validation_payload_compacts_large_venue_payloads() -> None:
    payload = build_ops_trading_validation_payload(
        status={
            "status": "ok",
            "run_id": "run-1",
            "computed_at": "2026-06-29T09:00:00+09:00",
            "summary": {
                "readiness": "probe",
                "diagnostic_status": "risk_repair",
                "fail_count": 1,
            },
            "readiness": "probe",
            "diagnostic_status": "risk_repair",
            "score": 72,
            "discipline_count": 18,
            "expected_discipline_count": 19,
            "venues": {
                "kis": {
                    "summary": {
                        "readiness": "probe",
                        "diagnostic_status": "risk_repair",
                        "fail_count": 1,
                    },
                    "diagnostic_status": "risk_repair",
                    "payload": {"huge": "x" * 80_000},
                    "lane_authority_summary": {"reduced_lane_count": 1},
                },
                "binance": {
                    "summary": {"readiness": "normal", "fail_count": 0},
                    "payload": {"huge": "y" * 80_000},
                    "lane_authority_summary": {"reduced_lane_count": 0},
                },
            },
            "lane_authority_summary": {"huge": "z" * 80_000},
            "bottlenecks": [{"detail": "b" * 20_000}],
            "primary_next_actions": [{"detail": "a" * 20_000}],
        },
        db_path=".runtime/trading_validation.db",
    )

    encoded = json.dumps(payload, ensure_ascii=False)
    assert len(encoded) < 16_000
    assert payload["diagnostic_status"] == "risk_repair"
    assert payload["venues"]["kis"]["summary"]["readiness"] == "probe"
    assert payload["venues"]["kis"]["summary"]["diagnostic_status"] == "risk_repair"
    assert payload["venues"]["kis"]["diagnostic_status"] == "risk_repair"
    assert payload["venues"]["kis"]["lane_authority_summary"] == {
        "reduced_lane_count": 1
    }
    assert "payload" not in payload["venues"]["kis"]
    assert payload["bottlenecks"][0]["detail"] == "b" * 220
    assert payload["primary_next_actions"][0]["detail"] == "a" * 220


def test_build_ops_trading_validation_payload_compacts_lane_authority_details() -> None:
    payload = build_ops_trading_validation_payload(
        status={
            "status": "ok",
            "run_id": "run-1",
            "computed_at": "2026-06-29T09:00:00+09:00",
            "summary": {"readiness": "probe"},
            "readiness": "probe",
            "score": 50,
            "discipline_count": 38,
            "expected_discipline_count": 38,
            "venues": {
                "binance": {
                    "readiness": "normal",
                    "lane_authority_summary": {
                        "version": "lane_authority_summary_v1",
                        "status": "warn",
                        "venue": "binance",
                        "execution_posture": "probe_allowed_scale_blocked",
                        "probe_policy": "small waiting-entry/probe blocks remain allowed",
                        "probe_lane_count": 12,
                        "probe_lane_names": [f"lane-{i}" for i in range(20)],
                        "scale_blocked_lane_count": 12,
                        "scale_blocked_lanes": [f"lane-{i}" for i in range(20)],
                        "reduced_lane_count": 12,
                        "insufficient_lanes": [f"lane-{i}" for i in range(20)],
                        "reduced_lanes": [
                            {
                                "venue": "binance",
                                "lane": f"lane-{i}",
                                "grade": "insufficient",
                                "action": "small_probe_until_sample_builds",
                                "authority_multiplier": 0.25,
                                "requires_waiting_entry": True,
                                "raw": "x" * 10_000,
                            }
                            for i in range(12)
                        ],
                    },
                }
            },
            "lane_authority_summary": {
                "version": "lane_authority_summary_v1",
                "status": "warn",
                "execution_posture": "probe_allowed_scale_blocked",
                "probe_policy": "small waiting-entry/probe blocks remain allowed",
                "probe_lane_count": 12,
                "probe_lane_names": [f"lane-{i}" for i in range(20)],
                "scale_blocked_lane_count": 12,
                "scale_blocked_lanes": [f"lane-{i}" for i in range(20)],
                "reduced_lane_count": 12,
                "insufficient_lanes": [f"lane-{i}" for i in range(20)],
                "reduced_lanes": [
                    {
                        "venue": "binance",
                        "lane": f"lane-{i}",
                        "grade": "insufficient",
                        "action": "small_probe_until_sample_builds",
                        "authority_multiplier": 0.25,
                        "requires_waiting_entry": True,
                        "raw": "x" * 10_000,
                    }
                    for i in range(12)
                ],
            },
        },
        db_path=".runtime/trading_validation.db",
    )

    top = payload["lane_authority_summary"]
    venue = payload["venues"]["binance"]["lane_authority_summary"]
    assert top["execution_posture"] == "probe_allowed_scale_blocked"
    assert venue["execution_posture"] == "probe_allowed_scale_blocked"
    assert top["probe_lane_count"] == 12
    assert top["scale_blocked_lane_count"] == 12
    assert top["probe_lane_names"] == [
        "lane-0",
        "lane-1",
        "lane-2",
        "lane-3",
        "lane-4",
        "lane-5",
        "lane-6",
        "lane-7",
    ]
    assert top["scale_blocked_lanes"] == [
        "lane-0",
        "lane-1",
        "lane-2",
        "lane-3",
        "lane-4",
        "lane-5",
        "lane-6",
        "lane-7",
    ]
    assert len(top["reduced_lanes"]) == 4
    assert len(venue["reduced_lanes"]) == 4
    assert top["insufficient_lanes"] == [
        "lane-0",
        "lane-1",
        "lane-2",
        "lane-3",
        "lane-4",
        "lane-5",
        "lane-6",
        "lane-7",
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "raw" not in serialized
    assert len(serialized) < 4_000


def test_runner_status_with_cover_distinguishes_direct_and_effective_alive() -> None:
    intelligence = ops_readiness.runner_status_with_cover(
        {"key": "intelligence", "label": "intelligence", "alive": True}
    )
    research = ops_readiness.runner_status_with_cover(
        {"key": "research", "label": "research", "alive": False},
        covered_by=intelligence,
    )

    assert intelligence["direct_alive"] is True
    assert intelligence["effective_alive"] is True
    assert research["direct_alive"] is False
    assert research["effective_alive"] is True
    assert research["status"] == "covered"
    assert research["covered_by"] == "intelligence"
    assert research["covered_by_label"] == "intelligence"


def test_light_runner_process_status_only_loads_matches_when_not_alive() -> None:
    calls: list[tuple[str, bool]] = []

    def status_fn(key: str, *, include_matches: bool = True) -> dict:
        calls.append((key, include_matches))
        if not include_matches:
            return {"key": key, "alive": key == "live"}
        return {"key": key, "alive": False, "matches": [{"pid": 1}]}

    assert ops_readiness.light_runner_process_status("live", status_fn)["alive"] is True
    assert ops_readiness.light_runner_process_status("dead", status_fn)["matches"] == [
        {"pid": 1}
    ]
    assert calls == [
        ("live", False),
        ("dead", False),
        ("dead", True),
    ]


def test_light_runner_process_status_can_scan_alive_matches_for_critical_runners() -> None:
    calls: list[tuple[str, bool]] = []

    def status_fn(key: str, *, include_matches: bool = True) -> dict:
        calls.append((key, include_matches))
        if not include_matches:
            return {"key": key, "alive": True, "matched_count": 1}
        return {
            "key": key,
            "alive": True,
            "matched_count": 2,
            "matches": [{"pid": 1}, {"pid": 2}],
        }

    status = ops_readiness.light_runner_process_status(
        "kis_block_trader",
        status_fn,
        scan_alive_matches=True,
    )

    assert status["matched_count"] == 2
    assert calls == [
        ("kis_block_trader", False),
        ("kis_block_trader", True),
    ]


def test_build_core_runner_processes_uses_light_status_and_code_staleness(tmp_path) -> None:
    base = tmp_path / "tradecraft"
    (base / "api").mkdir(parents=True)
    (base / "runtime").mkdir(parents=True)
    (base / "services").mkdir(parents=True)
    (base / "main.py").write_text("# main\n")
    (base / "config.py").write_text("# config\n")
    (base / "api" / "dashboard_payloads.py").write_text("# dashboard\n")
    (base / "runtime" / "runner.py").write_text("# runner\n")
    (base / "services" / "market.py").write_text("# market\n")
    calls: list[str] = []

    def status_for(key: str) -> dict:
        calls.append(key)
        return {
            "key": key,
            "label": key,
            "status": "running",
            "alive": True,
            "direct_alive": True,
            "effective_alive": True,
            "pid": 123,
            "started_at_epoch": None,
        }

    def staleness(process: dict, *, code_paths: list[Path]) -> dict:
        return {
            **process,
            "checked_paths": [path.relative_to(base).as_posix() for path in code_paths],
        }

    payload = ops_readiness.build_core_runner_processes(
        base=base,
        runner_status=status_for,
        apply_code_staleness=staleness,
    )

    assert "control" in payload
    assert "runtime" in payload
    assert "intelligence" in payload
    assert "naver_reports" in payload
    assert "strategy_insights" in payload
    assert "binance_block_trader" in payload
    assert "crypto_pattern_lab" in payload
    assert "main.py" in payload["control"]["checked_paths"]
    assert "config.py" in payload["control"]["checked_paths"]
    assert "api/dashboard_payloads.py" in payload["control"]["checked_paths"]
    assert "services/market.py" in payload["control"]["checked_paths"]
    assert payload["runtime"]["checked_paths"] == ["runtime/runner.py"]
    assert payload["intelligence"]["checked_paths"] == [
        "runtime/intelligence_runner.py",
        "runtime/research_runner.py",
        "services/research_pipeline.py",
    ]
    assert payload["naver_reports"]["checked_paths"] == [
        "runtime/naver_reports_runner.py",
        "services/naver_reports.py",
    ]
    assert payload["strategy_insights"]["checked_paths"] == [
        "runtime/strategy_insights_runner.py",
        "services/intelligence.py",
        "services/strategy_intelligence.py",
    ]
    assert "services/binance_block_trader.py" in payload["binance_block_trader"]["checked_paths"]
    assert "services/binance_manager_prompt.py" in payload["binance_block_trader"]["checked_paths"]
    assert "services/binance_manager_contract.py" in payload["binance_block_trader"]["checked_paths"]
    assert payload["crypto_pattern_lab"]["checked_paths"] == [
        "runtime/crypto_pattern_lab_runner.py",
        "services/crypto_pattern_lab.py",
    ]
    assert calls[:4] == ["control", "runtime", "intelligence", "research"]


def test_build_llm_operational_status_marks_failures_stale_after_runner_restart() -> None:
    assert hasattr(ops_payloads, "build_llm_operational_status")
    restarted_at = datetime(2026, 6, 2, 8, 0, tzinfo=timezone.utc).timestamp()

    payload = ops_payloads.build_llm_operational_status(
        block_status={
            "latest_manager_run_at": "2026-06-02T05:58:39+00:00",
            "latest_manager_status": "error",
            "latest_manager_mode": "error",
        },
        binance_block_status={
            "latest_manager_run_at": "2026-06-02T05:58:40+00:00",
            "latest_manager_status": "error",
            "latest_manager_mode": "llm",
        },
        market_schedule={
            "recent_runs": [
                {
                    "run_at": "2026-06-02T06:58:32+00:00",
                    "status": "error",
                    "mode": "error",
                    "error_message": "authentication token invalidated",
                }
            ]
        },
        processes={
            "kis_block_trader": {"started_at_epoch": restarted_at},
            "binance_block_trader": {"started_at_epoch": restarted_at},
            "market_judge": {"started_at_epoch": restarted_at},
        },
        configured=True,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        native_mode="sdk",
    )

    critical = payload["critical"]
    assert payload["configured"] is True
    assert payload["model"] == "gpt-5.5"
    assert critical["kis_block_manager"]["status"] == "error"
    assert critical["kis_block_manager"]["stale_after_restart"] is True
    assert critical["binance_block_manager"]["status"] == "error"
    assert critical["binance_block_manager"]["stale_after_restart"] is True
    assert critical["market_judge"]["status"] == "error"
    assert critical["market_judge"]["stale_after_restart"] is True


def test_llm_operational_status_keeps_unresolved_binance_manager_error_critical() -> None:
    payload = ops_payloads.build_llm_operational_status(
        block_status={"latest_manager_status": "ok"},
        binance_block_status={
            "latest_manager_run_at": "2026-07-08T00:15:00+00:00",
            "latest_manager_status": "blocked",
            "latest_manager_mode": "runtime",
            "latest_unresolved_manager_error": {
                "run_at": "2026-07-08T00:06:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
            "latest_manager_error_recovered": False,
        },
        market_schedule={"recent_runs": []},
        processes={
            "binance_block_trader": {
                "started_at_epoch": datetime(
                    2026,
                    7,
                    8,
                    0,
                    0,
                    tzinfo=timezone.utc,
                ).timestamp()
            }
        },
        configured=True,
    )

    critical = payload["critical"]["binance_block_manager"]
    assert critical["status"] == "error"
    assert critical["run_at"] == "2026-07-08T00:06:00+00:00"
    assert critical["latest_manager_status"] == "blocked"
    assert critical["error_message"] == "validation_repair_resolution_missing_from_model"
    assert critical["stale_after_restart"] is False

    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": []},
        trading_validation_status={"status": "ok", "summary": {"readiness": "clear"}},
        runner_liveness={"warnings": [], "stale_processes": [], "missing_processes": []},
        llm_operational=payload,
        semantic_checks={"warnings": []},
    )

    assert "binance_block_manager_last_run_failed" in summary["warnings"]


def test_llm_operational_status_suppresses_recovered_binance_manager_error_warning() -> None:
    payload = ops_payloads.build_llm_operational_status(
        block_status={"latest_manager_status": "ok"},
        binance_block_status={
            "latest_manager_run_at": "2026-07-08T00:15:00+00:00",
            "latest_manager_status": "error",
            "latest_manager_mode": "llm",
            "latest_manager_error_recovered": True,
            "latest_unresolved_manager_error": {},
            "latest_manager_error": {
                "run_at": "2026-07-08T00:06:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
        },
        market_schedule={"recent_runs": []},
        processes={},
        configured=True,
    )

    critical = payload["critical"]["binance_block_manager"]
    assert critical["status"] == "recovered"
    assert critical["latest_manager_status"] == "error"
    assert critical["latest_manager_error_recovered"] is True

    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": []},
        trading_validation_status={"status": "ok", "summary": {"readiness": "clear"}},
        runner_liveness={"warnings": [], "stale_processes": [], "missing_processes": []},
        llm_operational=payload,
        semantic_checks={"warnings": []},
    )

    assert "binance_block_manager_last_run_failed" not in summary["warnings"]


def test_disk_space_status_lives_in_ops_payloads_not_main() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()
    ops_source = (ROOT / "src/tradecraft/api/ops_payloads.py").read_text()

    assert "def _build_disk_space_status(" not in main_source
    assert "def build_disk_space_status(" in ops_source


def test_build_disk_space_status_uses_runtime_parent_and_thresholds(tmp_path) -> None:
    assert hasattr(ops_payloads, "build_disk_space_status")
    runtime_state_path = tmp_path / "state" / "runtime.json"
    runtime_state_path.parent.mkdir()

    class FakeDiskUsage:
        total = 10_000
        used = 9_200
        free = 800

    seen_paths: list[str] = []

    def fake_disk_usage(path):
        seen_paths.append(str(path))
        return FakeDiskUsage

    payload = ops_payloads.build_disk_space_status(
        runtime_state_path=str(runtime_state_path),
        disk_usage=fake_disk_usage,
        warn_bytes=2_000,
        critical_bytes=1_000,
    )

    assert seen_paths == [str(runtime_state_path.parent)]
    assert payload["status"] == "critical"
    assert payload["path"] == str(runtime_state_path.parent)
    assert payload["free_pct"] == 8.0
    assert payload["warn_free_bytes"] == 2_000
    assert payload["critical_free_bytes"] == 1_000


def test_build_runtime_storage_size_status_uses_four_and_six_gib_thresholds(
    tmp_path,
) -> None:
    runtime_state_path = tmp_path / ".runtime" / "runtime.json"

    warning = ops_payloads.build_runtime_storage_size_status(
        runtime_state_path=str(runtime_state_path),
        size_reader=lambda _path: 5 * 1024**3,
    )
    risk = ops_payloads.build_runtime_storage_size_status(
        runtime_state_path=str(runtime_state_path),
        size_reader=lambda _path: 7 * 1024**3,
    )

    assert warning["status"] == "warning"
    assert warning["warn_bytes"] == 4 * 1024**3
    assert risk["status"] == "risk"
    assert risk["risk_bytes"] == 6 * 1024**3


def test_ops_environment_signals_include_runtime_storage_pressure() -> None:
    payload = build_ops_environment_signals(
        admin_token_configured=True,
        disk_space_status={
            "status": "ok",
            "runtime_storage": {"status": "risk", "total_bytes": 7 * 1024**3},
        },
        live_execution={},
        readiness={},
        kill_switch_enabled=False,
        binance_kill_switch_enabled=False,
        memory_status={"status": "ok"},
        feature_enabled={},
    )

    assert "runtime_storage_risk" in payload["blockers"]


def test_append_trading_validation_ops_signals_marks_blockers_and_venue_warnings() -> None:
    blockers: list[str] = []
    warnings: list[str] = []

    append_trading_validation_ops_signals(
        {
            "stale": True,
            "summary": {
                "readiness": "blocked_by_validation",
                "fail_count": 0,
                "hard_fail_count": 1,
            },
            "venues": {
                "kis": {
                    "summary": {
                        "readiness": "probe",
                        "fail_count": 1,
                        "hard_fail_count": 0,
                        "diagnostic_fail_count": 1,
                    },
                    "payload": {"discipline_count": 19},
                    "lane_authority_summary": {"reduced_lane_count": 2},
                }
            },
        },
        blockers=blockers,
        warnings=warnings,
    )

    assert blockers == ["trading_validation_blocked"]
    assert "trading_validation_stale" in warnings
    assert "trading_validation_diagnostic_failures_kis" in warnings
    assert "trading_validation_probe_kis" in warnings
    assert "trading_validation_lane_authority_reduced_kis" in warnings


def test_append_trading_validation_ops_signals_prefers_venue_specific_advisories() -> None:
    blockers: list[str] = []
    warnings: list[str] = []

    append_trading_validation_ops_signals(
        {
            "summary": {
                "readiness": "probe",
                "fail_count": 3,
                "hard_fail_count": 0,
                "diagnostic_fail_count": 3,
            },
            "payload": {"discipline_count": 19},
            "lane_authority_summary": {"reduced_lane_count": 2},
            "venues": {
                "kis": {
                    "summary": {
                        "readiness": "probe",
                        "fail_count": 0,
                        "hard_fail_count": 0,
                        "diagnostic_fail_count": 0,
                    },
                    "payload": {"discipline_count": 19},
                    "lane_authority_summary": {"reduced_lane_count": 1},
                },
                "binance": {
                    "summary": {
                        "readiness": "normal",
                        "fail_count": 3,
                        "hard_fail_count": 0,
                        "diagnostic_fail_count": 3,
                    },
                    "payload": {"discipline_count": 19},
                    "lane_authority_summary": {"reduced_lane_count": 1},
                },
            },
        },
        blockers=blockers,
        warnings=warnings,
    )

    assert blockers == []
    assert "trading_validation_diagnostic_failures" not in warnings
    assert "trading_validation_probe" not in warnings
    assert "trading_validation_lane_authority_reduced" not in warnings
    assert "trading_validation_probe_kis" in warnings
    assert "trading_validation_lane_authority_reduced_kis" in warnings
    assert "trading_validation_diagnostic_failures_binance" in warnings
    assert "trading_validation_lane_authority_reduced_binance" in warnings


def test_blocked_strategy_validation_is_advisory_without_hard_fail() -> None:
    blockers: list[str] = []
    warnings: list[str] = []

    append_trading_validation_ops_signals(
        {
            "venues": {
                "binance": {
                    "summary": {
                        "readiness": "blocked_by_validation",
                        "diagnostic_status": "blocked",
                        "fail_count": 10,
                        "hard_fail_count": 0,
                        "hard_missing_count": 1,
                        "core_missing_count": 1,
                        "diagnostic_fail_count": 10,
                    },
                    "payload": {
                        "discipline_count": 19,
                        "disciplines": [
                            {"id": "profit_factor", "status": "fail"},
                            {"id": "risk_of_ruin", "status": "fail"},
                        ],
                    },
                }
            }
        },
        blockers=blockers,
        warnings=warnings,
    )

    assert blockers == []
    assert "trading_validation_strategy_blocked_binance" in warnings
    assert "trading_validation_diagnostic_failures_binance" in warnings


def test_build_ops_remediation_actions_maps_signals_to_operator_actions() -> None:
    actions = build_ops_remediation_actions(
        blockers=["trading_validation_blocked", "disk_space_critical"],
        warnings=[
            "restart_required",
            "memory_not_seeded",
            "llm_error_rate_high",
            "reports_db_stale",
            "binance_activity_pressure_open",
            "trading_validation_lane_authority_reduced_kis",
            "market_pulse_runner_duplicated",
        ],
        stale_processes=["market_judge"],
        missing_processes=["watchdog"],
        duplicate_processes=["market_pulse"],
    )

    by_id = {row["id"]: row for row in actions}
    assert by_id["review_trading_validation_failures"]["severity"] == "blocker"
    assert by_id["restart_stale_runners"]["endpoint"] == "/api/ops/restart"
    assert "market_judge" in by_id["restart_stale_runners"]["detail"]
    assert "market_pulse" in by_id["restart_stale_runners"]["detail"]
    assert by_id["seed_investment_memory"]["endpoint"] == "/api/memory/seed-current"
    assert by_id["review_llm_usage_errors"]["endpoint"] == "/api/llm/usage"
    assert by_id["cleanup_runtime_storage"]["severity"] == "blocker"
    assert by_id["refresh_research_pipeline"]["endpoint"] == "/api/reports/status"
    assert by_id["review_binance_activity_pressure"]["endpoint"] == (
        "/api/binance/blocks/status"
    )
    assert by_id["review_binance_activity_pressure"]["method"] == "GET"
    assert by_id["refresh_binance_crypto_research_context"]["endpoint"] == (
        "/api/crypto/research/run-once"
    )
    assert by_id["refresh_binance_crypto_research_context"]["method"] == "POST"
    assert by_id["refresh_binance_crypto_research_context"]["severity"] == "warn"
    assert by_id["refresh_binance_crypto_research_context"]["signals"] == [
        "binance_activity_pressure_open"
    ]
    assert by_id["restart_binance_recovery_runners"]["endpoint"] == "/api/ops/restart"
    assert by_id["restart_binance_recovery_runners"]["method"] == "POST"
    assert by_id["restart_binance_recovery_runners"]["request_payload"] == {
        "keys": ["binance_block_trader", "watchdog"]
    }
    assert by_id["restart_binance_recovery_runners"]["requires_confirmation"] is True
    assert by_id["restart_binance_recovery_runners"]["follow_up_actions"] == [
        {
            "id": "check_binance_status_after_restart",
            "label": "Binance 상태 재확인",
            "endpoint": "/api/binance/blocks/status",
            "method": "GET",
        },
        {
            "id": "run_binance_manager_after_restart",
            "label": "Binance 매니저 즉시 실행",
            "endpoint": "/api/binance/blocks/manager/run-once",
            "method": "POST",
            "request_payload": {"confirm_live_manager_run": True},
            "requires_confirmation": True,
        },
        {
            "id": "run_binance_executor_after_manager",
            "label": "Binance 실행 틱 확인 실행",
            "endpoint": "/api/binance/blocks/executor/tick",
            "method": "POST",
            "request_payload": {"confirm_live_executor_tick": True},
            "requires_confirmation": True,
        },
    ]
    assert by_id["review_lane_authority_reductions"]["endpoint"] == (
        "/api/trading/validation/status"
    )


def test_ops_remediation_actions_include_binance_contract_replay_current_error() -> None:
    actions = build_ops_remediation_actions(
        blockers=[],
        warnings=["binance_manager_contract_replay_current_error"],
        stale_processes=[],
        missing_processes=[],
        duplicate_processes=[],
    )

    by_id = {row["id"]: row for row in actions}
    assert by_id["review_binance_activity_pressure"]["signals"] == [
        "binance_manager_contract_replay_current_error"
    ]
    assert by_id["restart_binance_recovery_runners"]["signals"] == [
        "binance_manager_contract_replay_current_error"
    ]
    assert by_id["restart_binance_recovery_runners"]["request_payload"] == {
        "keys": ["binance_block_trader", "watchdog"]
    }


def test_ops_remediation_actions_include_binance_contract_replay_recovered() -> None:
    actions = build_ops_remediation_actions(
        blockers=[],
        warnings=["binance_manager_contract_replay_recovered"],
        stale_processes=[],
        missing_processes=[],
        duplicate_processes=[],
    )

    by_id = {row["id"]: row for row in actions}
    assert by_id["review_binance_activity_pressure"]["signals"] == [
        "binance_manager_contract_replay_recovered"
    ]
    assert by_id["restart_binance_recovery_runners"]["signals"] == [
        "binance_manager_contract_replay_recovered"
    ]
    assert by_id["restart_binance_recovery_runners"]["requires_confirmation"] is True
    assert by_id["restart_binance_recovery_runners"]["follow_up_actions"][-2:] == [
        {
            "id": "run_binance_manager_after_restart",
            "label": "Binance 매니저 즉시 실행",
            "endpoint": "/api/binance/blocks/manager/run-once",
            "method": "POST",
            "request_payload": {"confirm_live_manager_run": True},
            "requires_confirmation": True,
        },
        {
            "id": "run_binance_executor_after_manager",
            "label": "Binance 실행 틱 확인 실행",
            "endpoint": "/api/binance/blocks/executor/tick",
            "method": "POST",
            "request_payload": {"confirm_live_executor_tick": True},
            "requires_confirmation": True,
        },
    ]


def test_ops_remediation_actions_include_jue_wiki_action_reference_gap_repair() -> None:
    actions = build_ops_remediation_actions(
        blockers=[],
        warnings=[
            "kis_jue_wiki_action_reference_gap_unresolved",
            "binance_jue_wiki_action_reference_gap_unresolved",
        ],
        stale_processes=[],
        missing_processes=[],
        duplicate_processes=[],
    )

    by_id = {row["id"]: row for row in actions}
    assert by_id["run_jue_wiki_action_reference_reflection"] == {
        "id": "run_jue_wiki_action_reference_reflection",
        "label": "쥬 위키 근거 누락 반성 실행",
        "detail": (
            "KIS/Binance 매니저가 위키 기억을 판단 근거로 해소하지 못했습니다. "
            "메모리 반성 루프를 즉시 실행해 다음 판단에 위키 근거 또는 미사용 사유를 강제합니다."
        ),
        "severity": "warn",
        "endpoint": "/api/memory/reflections/run-due",
        "method": "POST",
        "signals": [
            "kis_jue_wiki_action_reference_gap_unresolved",
            "binance_jue_wiki_action_reference_gap_unresolved",
        ],
    }


def test_build_ops_runner_liveness_flags_stale_and_enabled_stopped_runners() -> None:
    liveness = build_ops_runner_liveness(
        processes={
            "kis_block_trader": {"direct_alive": False, "stale_process": False},
            "binance_block_trader": {"direct_alive": True, "stale_process": True},
            "market_judge": {"direct_alive": False, "stale_process": True},
            "watchdog": {"direct_alive": False, "stale_process": False},
            "market_pulse": {
                "direct_alive": True,
                "stale_process": False,
                "matched_count": 2,
            },
        },
        enabled={
            "kis_block_trader": True,
            "binance_block_trader": True,
            "market_judge": False,
            "watchdog": True,
            "crypto_alpha": True,
        },
    )

    assert liveness["stale_processes"] == [
        "binance_block_trader",
        "market_judge",
    ]
    assert liveness["missing_processes"] == [
        "kis_block_trader",
        "watchdog",
        "crypto_alpha",
    ]
    assert liveness["duplicate_processes"] == ["market_pulse"]
    assert liveness["warnings"] == [
        "restart_required",
        "kis_block_trader_runner_stopped",
        "watchdog_runner_stopped",
        "crypto_alpha_runner_stopped",
        "market_pulse_runner_duplicated",
    ]


def test_build_ops_environment_signals_marks_live_blockers_and_soft_warnings() -> None:
    signals = build_ops_environment_signals(
        admin_token_configured=False,
        disk_space_status={"status": "low"},
        live_execution={
            "kis": True,
            "binance_spot": True,
            "binance_futures": True,
            "upbit": False,
        },
        readiness={
            "kis_primary": False,
            "binance_spot": True,
            "binance_futures": False,
            "upbit": False,
        },
        kill_switch_enabled=True,
        binance_kill_switch_enabled=True,
        memory_status={"seeded": False, "validation_repair_backlog_count": 2},
        feature_enabled={
            "investment_memory": False,
            "live_evaluator": True,
            "market_judge": False,
            "market_pulse": False,
            "watchdog": False,
            "crypto_market_research": False,
            "crypto_alpha": True,
        },
    )

    assert signals["blockers"] == [
        "admin_token_not_configured",
        "kis_primary_not_ready_for_live_orders",
        "binance_futures_not_ready_for_live_orders",
    ]
    assert signals["warnings"] == [
        "disk_space_low",
        "kill_switch_enabled",
        "binance_kill_switch_enabled",
        "memory_not_seeded",
        "validation_repair_backlog_pending",
        "investment_memory_disabled",
        "market_judge_disabled",
        "market_pulse_disabled",
        "watchdog_disabled",
        "crypto_market_research_disabled",
    ]
    assert signals["live_trading_enabled"] is True
    assert signals["paper_mode"] is False


def test_finalize_ops_readiness_signals_merges_validation_llm_and_semantic_warnings() -> None:
    summary = finalize_ops_readiness_signals(
        environment_signals={
            "blockers": ["admin_token_not_configured"],
            "warnings": ["memory_not_seeded"],
        },
        trading_validation_status={
            "summary": {
                "readiness": "probe",
                "fail_count": 1,
                "hard_fail_count": 0,
                "diagnostic_fail_count": 1,
            },
            "payload": {"discipline_count": 19},
        },
        runner_liveness={
            "warnings": ["restart_required"],
            "stale_processes": ["market_judge"],
            "missing_processes": ["watchdog"],
        },
        llm_operational={
            "critical": {
                "kis_block_manager": {
                    "status": "error",
                    "stale_after_restart": False,
                },
                "binance_block_manager": {
                    "status": "error",
                    "stale_after_restart": False,
                },
                "market_judge": {
                    "status": "error",
                    "stale_after_restart": True,
                },
            }
        },
        semantic_checks={"warnings": ["semantic_check_pending", "memory_not_seeded"]},
    )

    assert summary["status"] == "red"
    assert summary["blockers"] == ["admin_token_not_configured"]
    assert summary["warnings"] == [
        "memory_not_seeded",
        "restart_required",
        "kis_block_manager_last_run_failed",
        "binance_block_manager_last_run_failed",
        "semantic_check_pending",
    ]
    assert summary["advisories"] == [
        "trading_validation_diagnostic_failures",
        "trading_validation_probe",
    ]
    assert summary["trading_validation_advisories"] == [
        "trading_validation_diagnostic_failures",
        "trading_validation_probe",
    ]
    assert summary["stale_processes"] == ["market_judge"]
    assert summary["missing_processes"] == ["watchdog"]
    action_ids = [row["id"] for row in summary["remediation_actions"]]
    assert "restart_stale_runners" in action_ids
    assert "seed_investment_memory" in action_ids
    assert "review_trading_validation_diagnostics" in action_ids


def test_ops_remediation_actions_include_runtime_session_config_review() -> None:
    actions = build_ops_remediation_actions(
        blockers=[],
        warnings=["runtime_sessions_missing"],
        stale_processes=[],
        missing_processes=[],
        duplicate_processes=[],
    )

    action = next(row for row in actions if row["id"] == "review_runtime_sessions_config")
    assert action["label"] == "Runtime 세션 설정 확인"
    assert action["severity"] == "warn"
    assert action["signals"] == ["runtime_sessions_missing"]


def test_finalize_ops_readiness_keeps_strategy_validation_advisory_green() -> None:
    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": []},
        trading_validation_status={
            "summary": {
                "readiness": "probe",
                "fail_count": 1,
                "hard_fail_count": 0,
                "diagnostic_fail_count": 1,
            },
            "payload": {"discipline_count": 19},
            "lane_authority_summary": {"reduced_lane_count": 1},
        },
        runner_liveness={
            "warnings": [],
            "stale_processes": [],
            "missing_processes": [],
        },
        llm_operational={"critical": {}},
        semantic_checks={"warnings": []},
    )

    assert summary["status"] == "green"
    assert summary["warnings"] == []
    assert summary["advisories"] == [
        "trading_validation_diagnostic_failures",
        "trading_validation_probe",
        "trading_validation_lane_authority_reduced",
    ]
    action_ids = [row["id"] for row in summary["remediation_actions"]]
    assert "review_trading_validation_diagnostics" in action_ids
    assert "review_lane_authority_reductions" in action_ids
    diagnostics = next(
        row
        for row in summary["remediation_actions"]
        if row["id"] == "review_trading_validation_diagnostics"
    )
    assert "19개 진단 fail" not in diagnostics["detail"]
    assert "진단 fail이 남아" in diagnostics["detail"]


def test_finalize_ops_readiness_splits_operational_and_advisory_actions() -> None:
    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": ["restart_required"]},
        trading_validation_status={
            "summary": {
                "readiness": "probe",
                "fail_count": 1,
                "hard_fail_count": 0,
                "diagnostic_fail_count": 1,
            },
            "payload": {"discipline_count": 19},
        },
        runner_liveness={
            "warnings": [],
            "stale_processes": ["jue_wiki"],
            "missing_processes": [],
            "duplicate_processes": [],
        },
        llm_operational={"critical": {}},
        semantic_checks={"warnings": []},
    )

    assert summary["status"] == "yellow"
    assert {row["id"] for row in summary["operational_remediation_actions"]} == {
        "restart_stale_runners"
    }
    assert "review_trading_validation_diagnostics" in {
        row["id"] for row in summary["advisory_actions"]
    }
    assert summary["remediation_actions"] == [
        *summary["operational_remediation_actions"],
        *summary["advisory_actions"],
    ]


def test_finalize_ops_readiness_keeps_strategy_validation_block_green() -> None:
    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": []},
        trading_validation_status={
            "venues": {
                "binance": {
                    "summary": {
                        "readiness": "blocked_by_validation",
                        "diagnostic_status": "blocked",
                        "total_score": 18.42,
                        "fail_count": 10,
                        "hard_fail_count": 0,
                        "hard_missing_count": 1,
                        "core_missing_count": 1,
                        "diagnostic_fail_count": 10,
                    },
                    "payload": {
                        "discipline_count": 19,
                        "disciplines": [
                            {"id": "profit_factor", "status": "fail"},
                            {"id": "risk_of_ruin", "status": "fail"},
                        ],
                    },
                }
            }
        },
        runner_liveness={"warnings": [], "stale_processes": [], "missing_processes": []},
        llm_operational={"critical": {}},
        semantic_checks={"warnings": []},
    )

    assert summary["status"] == "green"
    assert summary["blockers"] == []
    assert "trading_validation_strategy_blocked_binance" in summary["advisories"]
    details = {row["signal"]: row for row in summary["advisory_details"]}
    assert details["trading_validation_strategy_blocked_binance"]["readiness"] == (
        "blocked_by_validation"
    )
    assert "review_strategy_validation_blocks" in [
        row["id"] for row in summary["remediation_actions"]
    ]


def test_finalize_ops_readiness_exposes_validation_advisory_details() -> None:
    summary = finalize_ops_readiness_signals(
        environment_signals={"blockers": [], "warnings": []},
        trading_validation_status={
            "venues": {
                "kis": {
                    "summary": {
                        "readiness": "probe",
                        "diagnostic_status": "watch",
                        "total_score": 65.79,
                        "fail_count": 0,
                        "diagnostic_fail_count": 0,
                    },
                    "payload": {
                        "discipline_count": 19,
                        "summary": {
                            "active_revision_sample_count": 5,
                            "min_samples_to_scale": 30,
                        },
                    },
                    "lane_authority_summary": {"reduced_lane_count": 0},
                },
                "binance": {
                    "summary": {
                        "readiness": "normal",
                        "diagnostic_status": "risk_repair",
                        "total_score": 34.21,
                        "fail_count": 10,
                        "hard_fail_count": 0,
                        "diagnostic_fail_count": 10,
                        "active_revision_sample_count": 51,
                        "min_samples_to_scale": 30,
                    },
                    "payload": {
                        "discipline_count": 19,
                        "disciplines": [
                            {"id": f"failed_{index}", "status": "fail"}
                            for index in range(10)
                        ],
                    },
                    "lane_authority_summary": {
                        "reduced_lane_count": 2,
                        "weak_lanes": ["futures_short"],
                    },
                },
            }
        },
        runner_liveness={"warnings": [], "stale_processes": [], "missing_processes": []},
        llm_operational={"critical": {}},
        semantic_checks={"warnings": []},
    )

    details = {row["signal"]: row for row in summary["advisory_details"]}
    assert details["trading_validation_probe_kis"] == {
        "signal": "trading_validation_probe_kis",
        "venue": "kis",
        "readiness": "probe",
        "diagnostic_status": "watch",
        "score": 65.79,
        "fail_count": 0,
        "diagnostic_fail_count": 0,
        "sample_count": 5,
        "min_samples_to_scale": 30,
        "reduced_lane_count": 0,
        "failed_discipline_ids": [],
        "note": "표본 축적/probe 단계입니다. 탐색 거래와 대기진입을 유지하고, 검증될수록 sizing을 확대합니다.",
    }
    assert details["trading_validation_diagnostic_failures_binance"][
        "failed_discipline_ids"
    ] == [f"failed_{index}" for index in range(8)]
    assert (
        details["trading_validation_diagnostic_failures_binance"][
            "failed_discipline_count"
        ]
        == 10
    )
    assert details["trading_validation_diagnostic_failures_binance"][
        "omitted_failed_discipline_ids"
    ] == ["failed_8", "failed_9"]
    assert details["trading_validation_diagnostic_failures_binance"]["note"] == (
        "성과/위험 진단 실패가 남아 있어 표본 축적 거래는 유지하되 확대 조건을 더 치밀하게 검증합니다."
    )
    assert details["trading_validation_lane_authority_reduced_binance"][
        "weak_lanes"
    ] == ["futures_short"]


def test_build_ops_memory_payload_normalizes_counts_and_defaults() -> None:
    payload = build_ops_memory_payload(
        enabled=True,
        memory_status={
            "status": "ok",
            "seeded": True,
            "pending_event_count": "3",
            "reflection_count": "7",
            "latest_reflection_at": "2026-06-20T01:02:03+00:00",
            "scorecard_count": "5",
            "policy_rule_count": "9",
            "active_policy_rule_count": "2",
            "validation_repair_backlog_count": "4",
        },
    )

    assert payload == {
        "enabled": True,
        "seeded": True,
        "pending_reflection_count": 3,
        "reflection_count": 7,
        "latest_reflection_at": "2026-06-20T01:02:03+00:00",
        "scorecard_count": 5,
        "policy_rule_count": 9,
        "active_policy_rule_count": 2,
        "validation_repair_backlog_status": "clear",
        "validation_repair_backlog_count": 4,
        "status": "ok",
    }


def test_build_ops_service_section_payloads_preserve_endpoint_contracts() -> None:
    reports = build_ops_reports_payload(
        enabled=True,
        repository_status={"status": "ok", "count": 12},
        runner={"direct_alive": True},
        state_path=".runtime/research.json",
        interval_sec="900",
    )
    live_evaluator = build_ops_live_evaluator_payload(
        enabled=False,
        state_path=".runtime/live.json",
        edge_db_path=".runtime/live_edge.db",
        performance_db_path=".runtime/live_performance.db",
        interval_sec="300",
        runner={"direct_alive": False},
    )
    trading_validation = build_ops_trading_validation_payload(
        status={
            "status": "ok",
            "run_id": "run-1",
            "computed_at": "2026-06-20T02:00:00+00:00",
            "summary": {"readiness": "probe"},
            "readiness": "probe",
            "score": 71,
            "discipline_count": 19,
            "expected_discipline_count": 19,
            "venues": {"binance": {"readiness": "probe"}},
            "lane_authority_summary": {"reduced_lane_count": 1},
            "bottlenecks": ["cost"],
            "primary_next_actions": ["reduce churn"],
        },
        db_path=".runtime/trading_validation.db",
    )
    watchdog = build_ops_watchdog_payload(
        enabled=True,
        state_path=".runtime/watchdog.json",
        db_path=".runtime/watchdog.db",
        interval_sec="1800",
        runner={"direct_alive": True},
    )

    assert reports == {
        "enabled": True,
        "repository": {"status": "ok", "count": 12},
        "runner": {"direct_alive": True},
        "state_path": ".runtime/research.json",
        "interval_sec": 900,
    }
    assert live_evaluator["authority_endpoint"] == "/api/live/authority"
    assert live_evaluator["interval_sec"] == 300
    assert live_evaluator["enabled"] is False
    assert trading_validation["latest_run_id"] == "run-1"
    assert trading_validation["latest_at"] == "2026-06-20T02:00:00+00:00"
    assert trading_validation["status_endpoint"] == "/api/trading/validation/status"
    assert trading_validation["run_once_endpoint"] == (
        "/api/trading/validation/run-once"
    )
    assert trading_validation["lane_authority_summary"] == {"reduced_lane_count": 1}
    assert watchdog == {
        "enabled": True,
        "state_path": ".runtime/watchdog.json",
        "db_path": ".runtime/watchdog.db",
        "interval_sec": 1800,
        "runner": {"direct_alive": True},
        "status_endpoint": "/api/ops/watchdog/status",
    }


def test_build_ops_jue_wiki_payload_exposes_phase2_readiness_signals() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "open_lint_count": "21",
            "stale_page_count": "4",
            "active_page_count": "10",
            "wiki_repair_queue_open_count": "2",
            "wiki_repair_queue_resolved_count": "14",
            "repair_queue": {
                "open_count": 2,
                "resolved_count": 14,
                "by_scope": {"kis": {"open_count": 2, "resolved_count": 14}},
            },
            "latest_selection": {
                "created_at": "2026-06-28T01:00:00+00:00",
                "char_count": 92000,
                "max_chars": 100000,
            },
        },
        runner={
            "direct_alive": False,
            "state": {
                "repair": {"finished_at": "2026-06-28T01:05:00+00:00"},
            },
        },
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec="1800",
    )

    assert payload["wiki_open_lint_count"] == 21
    assert payload["wiki_stale_page_count"] == 4
    assert payload["wiki_last_selection_at"] == "2026-06-28T01:00:00+00:00"
    assert payload["wiki_last_repair_at"] == "2026-06-28T01:05:00+00:00"
    assert payload["wiki_repair_queue_open_count"] == 2
    assert payload["wiki_repair_queue_resolved_count"] == 14
    assert payload["repair_queue"]["by_scope"]["kis"]["open_count"] == 2
    assert payload["wiki_prompt_pressure"] == {
        "char_count": 92000,
        "max_chars": 100000,
        "ratio": 0.92,
    }
    assert payload["warnings"] == [
        "jue_wiki_runner_stopped",
        "jue_wiki_lint_findings_open",
        "jue_wiki_stale_pages_high",
        "jue_wiki_prompt_pressure_high",
    ]
    assert payload["advisories"] == ["jue_wiki_repair_queue_open"]
    assert payload["blockers"] == []


def test_build_ops_jue_wiki_payload_keeps_progressing_queue_advisory() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "page_count": 42,
            "repair_queue": {
                "open_count": 3,
                "resolved_count": 12,
                "repair_health": {
                    "status": "progressing",
                    "warning_signals": [],
                    "advisory_signals": ["jue_wiki_repair_queue_open"],
                    "progress_age_sec": 300,
                },
                "open_by_warning": {
                    "requested_symbol_summary_missing": 2,
                    "financials_missing": 1,
                },
            },
            "latest_selection": {
                "budget_report": {
                    "requested_symbol_count": 2,
                    "requested_symbol_missing_summary_count": 1,
                    "requested_symbol_prompt_omitted_count": 1,
                    "requested_symbol_degraded_summary_count": 1,
                }
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["warnings"] == []
    assert set(payload["advisories"]) == {
        "jue_wiki_repair_queue_open",
        "jue_wiki_requested_symbol_repair_pressure_open",
        "jue_wiki_financials_repair_pressure_open",
        "jue_wiki_requested_symbol_summaries_missing",
        "jue_wiki_requested_symbol_summaries_prompt_omitted",
        "jue_wiki_requested_symbol_summaries_degraded",
    }


def test_build_ops_jue_wiki_payload_keeps_non_integrity_growth_advisory() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "repair_queue": {
                "open_count": 30,
                "repair_health": {
                    "status": "idle",
                    "warning_signals": [],
                    "advisory_signals": [],
                },
                "by_lane": {
                    "evidence": {
                        "repair_health": {
                            "warning_signals": ["jue_wiki_repair_queue_growing"]
                        }
                    },
                    "strategy": {
                        "repair_health": {
                            "warning_signals": ["jue_wiki_repair_queue_stalled"]
                        }
                    },
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["warnings"] == []
    assert "jue_wiki_evidence_repair_queue_growing" in payload["advisories"]
    assert "jue_wiki_strategy_repair_queue_stalled" in payload["advisories"]


def test_kis_block_trader_payload_warns_on_unresolved_wiki_action_reference_gap() -> None:
    payload = build_ops_kis_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "manager_runs": [
                {
                    "id": 7,
                    "status": "ok",
                    "diagnostics": {
                        "jue_wiki_action_reference_memory_status": "active",
                        "jue_wiki_action_reference_memory_resolution_status": (
                            "unresolved"
                        ),
                        "jue_wiki_action_reference_status": "missing",
                        "blocker_tags": {
                            "unresolved_jue_wiki_action_reference_memory": 2,
                        },
                    },
                }
            ],
        },
        next_manager_run_at="2026-06-29T09:30:00+09:00",
    )

    assert payload["wiki_action_reference_gap"] == {
        "status": "missing",
        "resolution_status": "unresolved",
        "memory_status": "active",
        "run_id": 7,
        "blocker_tags": {
            "unresolved_jue_wiki_action_reference_memory": 2,
        },
    }
    assert "kis_jue_wiki_action_reference_gap_unresolved" in payload["warnings"]


def test_binance_block_trader_payload_warns_on_unresolved_wiki_action_reference_gap() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "manager_runs": [
                {
                    "id": 8,
                    "status": "ok",
                    "diagnostics": {
                        "jue_wiki_action_reference_memory_status": "active",
                        "jue_wiki_action_reference_memory_resolution_status": (
                            "unresolved"
                        ),
                        "jue_wiki_action_reference_status": "missing",
                        "blocker_tags": {
                            "unresolved_jue_wiki_action_reference_memory": 2,
                        },
                    },
                }
            ],
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=False,
        futures_live=True,
        upbit_live=False,
        account_risk_pct=0.3,
        max_total_exposure_usdt=100.0,
        max_symbol_exposure_pct=20.0,
        min_reward_risk=1.4,
        next_manager_run_at="2026-06-29T09:30:00+09:00",
    )

    assert payload["wiki_action_reference_gap"] == {
        "status": "missing",
        "resolution_status": "unresolved",
        "memory_status": "active",
        "run_id": 8,
        "blocker_tags": {
            "unresolved_jue_wiki_action_reference_memory": 2,
        },
    }
    assert "binance_jue_wiki_action_reference_gap_unresolved" in payload["warnings"]


def test_binance_block_trader_payload_warns_on_stale_runner_restart() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={"status": "ok"},
        runner={
            "direct_alive": True,
            "stale_process": True,
            "stale_code_process": True,
        },
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert payload["runner"]["stale_process"] is True
    assert "binance_runner_stale_restart_required" in payload["warnings"]


def test_binance_block_trader_payload_exposes_current_activity_pressure() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_decision_input": {
                "current_replay_pressure_status": "action_required",
                "current_replay_pressure_level": "high",
                "current_replay_pressure_source": "binance_activity_gap",
                "current_replay_zero_action_streak": 5,
                "current_replay_binance_zero_action_streak": 5,
                "current_replay_binance_activity_gap_status": "stale_binance_entries",
                "current_replay_binance_entry_stale_hours": 73.5,
                "current_replay_binance_candidate_symbols": ["ESPUSDT", "CHIPUSDT"],
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert payload["activity_pressure"] == {
        "status": "action_required",
        "level": "high",
        "source": "binance_activity_gap",
        "zero_action_streak": 5,
        "binance_zero_action_streak": 5,
        "activity_gap_status": "stale_binance_entries",
        "entry_stale_hours": 73.5,
        "candidate_symbols": ["ESPUSDT", "CHIPUSDT"],
    }
    assert "binance_activity_pressure_open" in payload["warnings"]


def test_binance_block_trader_payload_exposes_activity_repair_actions_for_candidate_symbols() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_decision_input": {
                "current_replay_pressure_status": "action_required",
                "current_replay_pressure_level": "high",
                "current_replay_pressure_source": "binance_activity_gap",
                "current_replay_binance_candidate_symbols": [
                    "ESPUSDT",
                    "CHIPUSDT",
                    "",
                    None,
                ],
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert payload["activity_repair_actions"] == [
        {
            "id": "refresh_binance_crypto_research_context",
            "label": "Binance 후보 리서치 갱신",
            "detail": (
                "활동 공백 후보의 최신 뉴스, 구조, 근거를 다시 수집해 "
                "research_only/insufficient gate를 줄입니다."
            ),
            "severity": "warn",
            "endpoint": "/api/crypto/research/run-once",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
            "request_payload": {"symbols": ["ESPUSDT", "CHIPUSDT"]},
        },
        {
            "id": "collect_binance_market_structure",
            "label": "Binance 시장 구조 수집",
            "detail": (
                "후보 심볼의 kline/market-structure 근거를 갱신해 "
                "pattern prior와 live crosscheck 결손을 줄입니다."
            ),
            "severity": "warn",
            "endpoint": "/api/crypto/research/collect",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
            "request_payload": {"symbols": ["ESPUSDT", "CHIPUSDT"]},
        },
        {
            "id": "refresh_binance_alpha_context",
            "label": "Binance 알파 컨텍스트 갱신",
            "detail": (
                "알파 컨텍스트를 새로 수집해 confidence/live-authority "
                "판정에 최신 후보 근거를 반영합니다."
            ),
            "severity": "warn",
            "endpoint": "/api/crypto/alpha/collect",
            "method": "POST",
            "signals": ["binance_activity_pressure_open"],
        },
    ]


def test_binance_block_trader_payload_exposes_read_only_entry_activity() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "entry_activity": {
                "version": "binance_entry_activity_v1",
                "status": "stale_binance_entries",
                "latest_binance_entry_at": "2026-07-05T00:00:00+00:00",
                "latest_binance_entry_market": "futures",
                "latest_upbit_entry_at": "2026-07-07T23:00:00+00:00",
                "binance_entry_stale_hours": 73.5,
                "binance_entry_count": 2,
                "upbit_entry_count": 4,
                "raw": "x" * 20_000,
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert payload["entry_activity"] == {
        "version": "binance_entry_activity_v1",
        "status": "stale_binance_entries",
        "latest_binance_entry_at": "2026-07-05T00:00:00+00:00",
        "latest_binance_entry_market": "futures",
        "latest_upbit_entry_at": "2026-07-07T23:00:00+00:00",
        "binance_entry_stale_hours": 73.5,
        "binance_entry_count": 2,
        "upbit_entry_count": 4,
    }
    assert "warnings" not in payload


def test_binance_block_trader_payload_exposes_contract_replay_recovery() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_decision_input": {
                "contract_replay_status": "stored_error_resolved_by_current_contract",
                "stored_error_message": "missing required field action_type",
                "current_contract_error": "",
                "action_count": 1,
                "current_replay_action_count": 3,
                "current_replay_auto_action_count": 2,
                "current_replay_action_sections": [
                    "create_blocks",
                    "update_blocks",
                ],
                "current_replay_hold_summary": "stored failure now replays",
                "current_replay_watch_symbols": ["BTCUSDT", "ETHUSDT"],
                "current_replay_next_triggers": [
                    {
                        "symbol": "ETHUSDT",
                        "market": "futures",
                        "condition": "pattern prior recovers",
                        "price": 0.0,
                        "reason": "pattern prior missing",
                    }
                ],
                "current_replay_data_gaps": ["pattern prior missing"],
                "current_replay_auto_create_preview": [
                    {
                        "symbol": "ETHUSDT",
                        "market": "futures",
                        "side": "short",
                        "entry_style": "wait_for_price",
                        "entry_trigger_price": 2475.0,
                        "entry_trigger_operator": ">=",
                        "entry_price": 2475.0,
                        "target_price": 2300.0,
                        "stop_price": 2525.0,
                        "qty": 0.01,
                        "quote_budget_usdt": 24.75,
                        "min_executable_notional_usdt": 20.0,
                        "min_executable_qty": 0.008081,
                        "notional_estimate_usdt": 24.75,
                        "auto_materialized_reason": (
                            "manager_selected_probe_waiting_block_without_create_action"
                        ),
                        "raw": "drop me",
                    }
                ],
                "raw_replay_payload": {"heavy": "drop me"},
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert payload["manager_contract_replay"] == {
        "contract_replay_status": "stored_error_resolved_by_current_contract",
        "stored_error_message": "missing required field action_type",
        "action_count": 1,
        "current_replay_action_count": 3,
        "current_replay_auto_action_count": 2,
        "current_replay_action_sections": ["create_blocks", "update_blocks"],
        "current_replay_hold_summary": "stored failure now replays",
        "current_replay_watch_symbols": ["BTCUSDT", "ETHUSDT"],
        "current_replay_next_triggers": [
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "condition": "pattern prior recovers",
                "price": 0.0,
                "reason": "pattern prior missing",
            }
        ],
        "current_replay_data_gaps": ["pattern prior missing"],
        "current_replay_auto_create_preview": [
            {
                "symbol": "ETHUSDT",
                "market": "futures",
                "side": "short",
                "entry_style": "wait_for_price",
                "entry_trigger_price": 2475.0,
                "entry_trigger_operator": ">=",
                "entry_price": 2475.0,
                "target_price": 2300.0,
                "stop_price": 2525.0,
                "qty": 0.01,
                "quote_budget_usdt": 24.75,
                "min_executable_notional_usdt": 20.0,
                "min_executable_qty": 0.008081,
                "notional_estimate_usdt": 24.75,
                "auto_materialized_reason": (
                    "manager_selected_probe_waiting_block_without_create_action"
                ),
            }
        ],
    }
    assert "binance_manager_contract_replay_recovered" in payload["warnings"]


def test_binance_block_trader_payload_exposes_current_contract_replay_error() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_decision_input": {
                "contract_replay_status": "current_contract_error",
                "stored_error_message": "validation_repair_resolution_missing_from_model",
                "current_contract_error": (
                    "binance_activity_gap_resolution_missing_from_model"
                ),
                "action_count": 0,
                "current_replay_action_count": 0,
                "raw_replay_payload": {"heavy": "drop me"},
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=False,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert payload["manager_contract_replay"] == {
        "contract_replay_status": "current_contract_error",
        "stored_error_message": "validation_repair_resolution_missing_from_model",
        "current_contract_error": (
            "binance_activity_gap_resolution_missing_from_model"
        ),
        "action_count": 0,
        "current_replay_action_count": 0,
    }
    assert "binance_manager_contract_replay_current_error" in payload["warnings"]


def test_binance_block_trader_payload_warns_on_unresolved_manager_error() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_manager_status": "error",
            "latest_unresolved_manager_error": {
                "run_at": "2026-07-08T00:00:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
            "latest_manager_error_recovered": False,
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert "binance_block_manager_last_run_failed" in payload["warnings"]


def test_binance_block_trader_payload_suppresses_replay_recovered_manager_error() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_manager_status": "error",
            "latest_unresolved_manager_error": {
                "run_at": "2026-07-08T00:00:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
            "latest_manager_error_recovered": False,
            "latest_decision_input": {
                "contract_replay_status": "stored_error_resolved_by_current_contract",
                "stored_error_message": "validation_repair_resolution_missing_from_model",
                "current_contract_error": "",
                "action_count": 0,
                "current_replay_action_count": 0,
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert "binance_block_manager_last_run_failed" not in payload.get("warnings", [])
    assert payload["manager_contract_replay"]["contract_replay_status"] == (
        "stored_error_resolved_by_current_contract"
    )


def test_binance_block_trader_payload_suppresses_stale_manager_error_after_restart() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_manager_status": "error",
            "latest_unresolved_manager_error": {
                "run_at": "2026-07-08T00:00:00+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "validation_repair_resolution_missing_from_model",
            },
            "latest_manager_error_recovered": False,
        },
        runner={
            "direct_alive": True,
            "started_at_epoch": datetime(
                2026,
                7,
                8,
                1,
                0,
                tzinfo=timezone.utc,
            ).timestamp(),
        },
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct=0.25,
        max_total_exposure_usdt=0.0,
        max_symbol_exposure_pct=25.0,
        min_reward_risk=1.3,
        next_manager_run_at="2026-07-08T08:00:00+09:00",
    )

    assert "binance_block_manager_last_run_failed" not in payload.get(
        "warnings",
        [],
    )
    assert payload["status"]["latest_manager_error_stale_after_restart"] is True
    assert payload["status"]["latest_stale_manager_error"] == {
        "run_at": "2026-07-08T00:00:00+00:00",
        "status": "error",
        "mode": "llm",
        "error_message": "validation_repair_resolution_missing_from_model",
    }
    assert "latest_unresolved_manager_error" not in payload["status"]
    assert "latest_manager_error" not in payload["status"]


def test_main_wires_binance_status_into_llm_operational_payload() -> None:
    main_source = (ROOT / "src/tradecraft/main.py").read_text()

    assert "binance_block_status=binance_block_status" in main_source


def test_build_ops_jue_wiki_payload_exposes_phase3_application_fields() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "page_count": 10,
            "application": {
                "effectiveness_count": 12,
                "degraded_count": 11,
                "latest_recommendation": {
                    "decision_scope": "kis",
                    "recommended_mode": "assist",
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["application"]["effectiveness_count"] == 12
    assert payload["application"]["degraded_count"] == 11
    assert (
        payload["application"]["latest_recommendation"]["recommended_mode"]
        == "assist"
    )
    assert "jue_wiki_effectiveness_degraded_high" in payload["warnings"]


def test_build_ops_jue_wiki_payload_exposes_requested_symbol_coverage_gaps() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "page_count": 42,
            "latest_selection": {
                "created_at": "2026-06-28T01:00:00+00:00",
                "budget_report": {
                    "requested_symbol_count": 4,
                    "requested_symbol_available_summary_count": 1,
                    "requested_symbol_available_summary_symbols": ["005930"],
                    "requested_symbol_missing_summary_count": 2,
                    "requested_symbol_missing_summary_symbols": ["000660", "402340"],
                    "requested_symbol_prompt_omitted_count": 1,
                    "requested_symbol_prompt_omitted_symbols": ["178920"],
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["requested_symbol_coverage"] == {
        "requested_count": 4,
        "available_summary_count": 1,
        "available_summary_symbols": ["005930"],
        "missing_summary_count": 2,
        "missing_summary_symbols": ["000660", "402340"],
        "prompt_omitted_count": 1,
        "prompt_omitted_symbols": ["178920"],
    }
    assert "jue_wiki_requested_symbol_summaries_missing" in payload["warnings"]
    assert (
        "jue_wiki_requested_symbol_summaries_prompt_omitted"
        in payload["advisories"]
    )


def test_build_ops_jue_wiki_payload_exposes_degraded_requested_symbol_summaries() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "page_count": 42,
            "latest_selection": {
                "created_at": "2026-06-28T01:00:00+00:00",
                "budget_report": {
                    "requested_symbol_count": 2,
                    "requested_symbol_available_summary_count": 2,
                    "requested_symbol_available_summary_symbols": [
                        "005930",
                        "000660",
                    ],
                    "requested_symbol_degraded_summary_count": 1,
                    "requested_symbol_degraded_summary_symbols": ["005930"],
                    "requested_symbol_degraded_summary_reasons": [
                        {
                            "symbol": "005930",
                            "freshness": "stale",
                            "quality_status": "weak",
                            "quality_warnings": ["valuation_stale_gt_30d"],
                        }
                    ],
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["requested_symbol_coverage"]["degraded_summary_count"] == 1
    assert payload["requested_symbol_coverage"]["degraded_summary_symbols"] == [
        "005930"
    ]
    assert payload["requested_symbol_coverage"]["degraded_summary_reasons"] == [
        {
            "symbol": "005930",
            "freshness": "stale",
            "quality_status": "weak",
            "quality_warnings": ["valuation_stale_gt_30d"],
        }
    ]
    assert "jue_wiki_requested_symbol_summaries_degraded" in payload["advisories"]


def test_build_ops_jue_wiki_payload_exposes_repair_pressure_summary() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "repair_queue": {
                "open_count": 3,
                "resolved_count": 1,
                "open_by_action_type": {
                    "refresh_requested_symbol_summary": 2,
                    "refresh_symbol_financials": 1,
                },
                "open_by_warning": {
                    "requested_symbol_summary_missing": 2,
                    "financials_missing": 1,
                },
                "open_symbols": ["000660", "245450", "BTCUSDT"],
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["repair_pressure"] == {
        "open_count": 3,
        "resolved_count": 1,
        "open_symbol_count": 3,
        "open_symbols": ["000660", "245450", "BTCUSDT"],
        "primary_action_type": "refresh_requested_symbol_summary",
        "primary_warning": "requested_symbol_summary_missing",
        "open_by_action_type": {
            "refresh_requested_symbol_summary": 2,
            "refresh_symbol_financials": 1,
        },
        "open_by_warning": {
            "requested_symbol_summary_missing": 2,
            "financials_missing": 1,
        },
    }
    assert (
        "jue_wiki_requested_symbol_repair_pressure_open" in payload["advisories"]
    )
    assert "jue_wiki_financials_repair_pressure_open" in payload["advisories"]


def test_build_ops_jue_wiki_payload_warns_for_degraded_summary_repair_pressure() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "repair_queue": {
                "open_count": 1,
                "resolved_count": 0,
                "open_by_action_type": {
                    "refresh_requested_symbol_summary": 1,
                },
                "open_by_warning": {
                    "requested_symbol_summary_degraded": 1,
                },
                "open_symbols": ["005930"],
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["repair_pressure"]["primary_warning"] == (
        "requested_symbol_summary_degraded"
    )
    assert (
        "jue_wiki_requested_symbol_repair_pressure_open" in payload["advisories"]
    )


def test_build_ops_jue_wiki_payload_does_not_warn_for_mixed_effectiveness_pool() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "page_count": 125,
            "application": {
                "effectiveness_count": 125,
                "degraded_count": 20,
                "latest_recommendation": {
                    "decision_scope": "binance",
                    "recommended_mode": "assist",
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert "jue_wiki_effectiveness_degraded_high" not in payload["warnings"]


def test_merge_section_readiness_signals_promotes_jue_wiki_top_level_warnings() -> None:
    readiness = merge_section_readiness_signals(
        {
            "status": "green",
            "blockers": [],
            "warnings": [],
            "remediation_actions": [],
        },
        {
            "warnings": ["jue_wiki_prompt_pressure_high"],
            "blockers": ["jue_wiki_unavailable"],
        },
    )
    payload = ops_readiness.build_ops_readiness_payload(
        readiness_signals=readiness,
        checked_at="2026-06-28T00:00:00+00:00",
        processes={},
        admin_token_configured=True,
        kis_ready=True,
        kis_rate_limit={},
        llm_ready=True,
        disk_space={"status": "ok"},
        llm={},
        llm_usage={},
        semantic_checks={},
        codex_native={},
        telegram_ready=True,
        live_trading_enabled=False,
        paper_mode=True,
        kill_switch={"enabled": False},
        sections={"jue_wiki": {"warnings": ["jue_wiki_prompt_pressure_high"]}},
        next_market_open_at="",
    )

    assert payload["status"] == "red"
    assert payload["warnings"] == ["jue_wiki_prompt_pressure_high"]
    assert payload["blockers"] == ["jue_wiki_unavailable"]


def test_merge_section_readiness_signals_keeps_advisory_remediation_actions() -> None:
    payload = merge_section_readiness_signals(
        {
            "status": "green",
            "blockers": [],
            "warnings": [],
            "advisories": ["trading_validation_lane_authority_reduced_binance"],
            "stale_processes": [],
            "missing_processes": [],
            "duplicate_processes": [],
            "remediation_actions": [],
        },
        {"warnings": ["trading_validation_stale_binance"]},
    )

    action_ids = [row["id"] for row in payload["remediation_actions"]]
    assert "review_lane_authority_reductions" in action_ids
    assert "refresh_trading_validation" in action_ids


def test_build_ops_market_trader_crypto_payloads_preserve_endpoint_contracts() -> None:
    market_judge = build_ops_market_judge_payload(
        enabled=True,
        status={"status": "ok", "latest_run_at": "2026-06-20T09:00:00+09:00"},
        schedule={"clock": {"phase": "regular"}},
    )
    market_pulse = build_ops_market_pulse_payload(
        enabled=False,
        status={"status": "disabled"},
    )
    kis_blocks = build_ops_kis_block_trader_payload(
        enabled=True,
        status={"latest_manager_run_at": "2026-06-20T09:30:00+09:00"},
        next_manager_run_at="2026-06-20T10:00:00+09:00",
    )
    binance_blocks = build_ops_binance_block_trader_payload(
        enabled=True,
        status={"latest_manager_run_at": "2026-06-20T00:30:00+00:00"},
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=False,
        upbit_live=True,
        account_risk_pct="1.5",
        max_total_exposure_usdt="250",
        max_symbol_exposure_pct="12",
        min_reward_risk="1.8",
        next_manager_run_at="2026-06-20T01:00:00+00:00",
    )
    crypto_research = build_ops_crypto_market_research_payload(
        enabled=True,
        status={"status": "ok"},
        runner={"direct_alive": False},
        model="gpt-5.5",
        reasoning_effort="high",
        feature_interval_sec="60",
        llm_interval_sec="1800",
        max_symbols="300",
        llm_top_symbols="60",
        kline_intervals=["5m", "15m"],
        regime_enabled=True,
        squeeze_guard_enabled=False,
        auto_universe_enabled=True,
        auto_universe_limit="300",
    )
    crypto_alpha = build_ops_crypto_alpha_payload(
        enabled=True,
        status={"status": "ok"},
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        crawl_interval_sec="900",
        outcome_interval_sec="3600",
        context_limit="190000",
        source_ids="coinglass, binance-announcements,,coinmarketcal",
    )

    assert market_judge == {
        "enabled": True,
        "status": {"status": "ok", "latest_run_at": "2026-06-20T09:00:00+09:00"},
        "schedule": {"clock": {"phase": "regular"}},
    }
    assert market_pulse == {
        "enabled": False,
        "status": {"status": "disabled"},
    }
    assert kis_blocks == {
        "enabled": True,
        "status": {"latest_manager_run_at": "2026-06-20T09:30:00+09:00"},
        "next_manager_run_at": "2026-06-20T10:00:00+09:00",
    }
    assert binance_blocks == {
        "enabled": True,
        "status": {"latest_manager_run_at": "2026-06-20T00:30:00+00:00"},
        "execution": {
            "spot_mode": "live",
            "futures_mode": "paper",
            "upbit_spot_mode": "live",
        },
        "runner": {"direct_alive": True},
        "model": "gpt-5.5",
        "reasoning_effort": "xhigh",
        "risk": {
            "account_risk_pct": 1.5,
            "max_total_exposure_usdt": 250.0,
            "max_symbol_exposure_pct": 12.0,
            "min_reward_risk": 1.8,
        },
        "next_manager_run_at": "2026-06-20T01:00:00+00:00",
    }
    assert crypto_research == {
        "enabled": True,
        "status": {"status": "ok"},
        "runner": {"direct_alive": False},
        "model": "gpt-5.5",
        "reasoning_effort": "high",
        "feature_interval_sec": 60,
        "llm_interval_sec": 1800,
        "max_symbols": 300,
        "llm_top_symbols": 60,
        "kline_intervals": ["5m", "15m"],
        "regime_enabled": True,
        "squeeze_guard_enabled": False,
        "auto_universe_enabled": True,
        "auto_universe_limit": 300,
    }
    assert crypto_alpha == {
        "enabled": True,
        "status": {"status": "ok"},
        "runner": {"direct_alive": True},
        "model": "gpt-5.5",
        "reasoning_effort": "xhigh",
        "crawl_interval_sec": 900,
        "outcome_interval_sec": 3600,
        "context_limit": 190000,
        "source_ids": ["coinglass", "binance-announcements", "coinmarketcal"],
    }


def test_build_ops_market_judge_payload_compacts_recent_runs_for_readiness() -> None:
    payload = build_ops_market_judge_payload(
        enabled=True,
        status={
            "status": "ok",
            "db_path": "/tmp/market_judgment.db",
            "latest_run_at": "2026-06-26T06:30:48+00:00",
            "latest_run_status": "ok",
            "latest_run_mode": "llm",
        },
        schedule={
            "status": "ok",
            "clock": {
                "session": "regular",
                "is_market_open": True,
                "raw": "x" * 20_000,
            },
            "next_llm_due_at": "2026-06-26T07:00:48+00:00",
            "recent_runs": [
                {
                    "id": 502,
                    "run_at": "2026-06-26T06:30:48+00:00",
                    "market_session": "post_close_review",
                    "status": "ok",
                    "mode": "llm",
                    "model": "gpt-5.5",
                    "error_message": "",
                    "source_snapshot": {"focus_symbols": ["005930"] * 500},
                    "prompt": {"large": "p" * 20_000},
                    "response": {"large": "r" * 20_000},
                }
            ],
        },
    )

    assert payload["schedule"]["clock"] == {
        "session": "regular",
        "is_market_open": True,
    }
    assert payload["schedule"]["recent_runs"] == [
        {
            "id": 502,
            "run_at": "2026-06-26T06:30:48+00:00",
            "market_session": "post_close_review",
            "status": "ok",
            "mode": "llm",
            "model": "gpt-5.5",
        }
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "source_snapshot" not in serialized
    assert "prompt" not in serialized
    assert "response" not in serialized
    assert len(serialized) < 2_000


def test_build_ops_market_pulse_payload_compacts_latest_for_readiness() -> None:
    payload = build_ops_market_pulse_payload(
        enabled=True,
        status={
            "status": "ok",
            "db_path": "/tmp/market_pulse.db",
            "latest": {
                "status": "ok",
                "captured_at": "2026-06-26T06:30:00+00:00",
                "trading_day": "2026-06-26",
                "regime": "risk_on",
                "score": 72,
                "risk_flags": ["basis_pressure"],
                "data_gaps": [],
                "indices": [
                    {
                        "code": "KOSPI",
                        "name": "KOSPI",
                        "value": 2840.2,
                        "change_pct": 1.2,
                        "source_url": "https://example.com/" + ("x" * 20_000),
                    }
                ],
                "investor_flows": [
                    {
                        "market": "KOSPI",
                        "bias": "foreign_buy",
                        "foreign_net_buy_100m_krw": 1200,
                        "institution_net_buy_100m_krw": -300,
                        "raw": "i" * 20_000,
                    }
                ],
                "sectors": {"raw": "s" * 20_000},
                "score_components": {"raw": "c" * 20_000},
            },
        },
    )

    latest = payload["status"]["latest"]
    assert latest["regime"] == "risk_on"
    assert latest["indices"] == [
        {
            "code": "KOSPI",
            "name": "KOSPI",
            "value": 2840.2,
            "change_pct": 1.2,
        }
    ]
    assert latest["investor_flows"] == [
        {
            "market": "KOSPI",
            "bias": "foreign_buy",
            "foreign_net_buy_100m_krw": 1200,
            "institution_net_buy_100m_krw": -300,
        }
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "source_url" not in serialized
    assert "score_components" not in serialized
    assert "sectors" not in serialized
    assert len(serialized) < 2_000


def test_build_ops_binance_block_trader_payload_compacts_heavy_status_sections() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "db_path": ".runtime/binance_blocks.db",
            "block_count": 243,
            "open_block_count": 2,
            "order_count": 4170,
            "manager_run_count": 1595,
            "latest_manager_run_at": "2026-06-28T20:20:02+00:00",
            "latest_manager_status": "ok",
            "execution_mode": "live",
            "performance": {
                "sample_count": 19,
                "win_rate_pct": 31.5,
                "avg_r_multiple": -0.27,
                "realized_pnl_usdt": -1.13,
                "profit_factor": 0.43,
                "symbol_scorecards": [
                    {
                        "symbol": f"SYM{i}USDT",
                        "sample_count": i,
                        "raw": "s" * 5_000,
                    }
                    for i in range(12)
                ],
            },
            "risk": {
                "account_risk_pct": 0.25,
                "max_total_exposure_usdt": 0,
                "max_symbol_exposure_pct": 25,
                "min_reward_risk": 1.3,
                "lane_risk_multipliers": {"futures:short": 0.7, "raw": "r" * 5_000},
                "lane_performance_multipliers": {"spot:long": 0.5},
            },
            "growth_unlock": {
                "version": "binance_growth_unlock_v1",
                "phase": "rebuilding",
                "can_leave_edge_rebuild": False,
                "criteria": [
                    {
                        "id": "win_rate",
                        "label": "recent win rate",
                        "current": 31.5,
                        "target": ">= 48%",
                        "passed": False,
                        "raw": "c" * 5_000,
                    }
                ],
                "action_permissions": {
                    "new_waiting_entry_probe": True,
                    "immediate_entry": False,
                },
                "next_missions": [{"raw": "m" * 10_000}],
            },
            "growth_governor": {
                "status": "edge_rebuild",
                "mode": "edge_rebuild",
                "allow_new_blocks": True,
                "max_new_blocks": 1,
                "require_waiting_entry": True,
                "aggression_multiplier": 0.5,
                "weak_lanes": [f"lane-{i}" for i in range(20)],
                "metrics": {
                    "win_rate_pct": 31.5,
                    "avg_r_multiple": -0.27,
                    "raw": "g" * 10_000,
                },
            },
            "live_authority": {
                "status": "ok",
                "live_grade": "restricted",
                "performance_lanes": [{"lane": "futures", "raw": "l" * 20_000}],
                "scorecards": [{"raw": "x" * 20_000}],
            },
        },
        runner={"direct_alive": True},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct="0.25",
        max_total_exposure_usdt="0",
        max_symbol_exposure_pct="25",
        min_reward_risk="1.3",
        next_manager_run_at="2026-06-28T20:30:00+00:00",
    )

    status = payload["status"]
    assert status["performance"] == {
        "sample_count": 19,
        "win_rate_pct": 31.5,
        "avg_r_multiple": -0.27,
        "realized_pnl_usdt": -1.13,
        "profit_factor": 0.43,
    }
    assert status["risk"] == {
        "account_risk_pct": 0.25,
        "max_total_exposure_usdt": 0,
        "max_symbol_exposure_pct": 25,
        "min_reward_risk": 1.3,
    }
    assert status["growth_unlock"]["phase"] == "rebuilding"
    assert status["growth_unlock"]["criteria"] == [
        {
            "id": "win_rate",
            "label": "recent win rate",
            "current": 31.5,
            "target": ">= 48%",
            "passed": False,
        }
    ]
    assert status["growth_governor"]["weak_lanes"] == [
        "lane-0",
        "lane-1",
        "lane-2",
        "lane-3",
        "lane-4",
        "lane-5",
        "lane-6",
        "lane-7",
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "symbol_scorecards" not in serialized
    assert "next_missions" not in serialized
    assert "lane_risk_multipliers" not in serialized
    assert len(serialized) < 5_000


def test_build_ops_binance_block_trader_payload_separates_recovered_manager_error() -> None:
    payload = build_ops_binance_block_trader_payload(
        enabled=True,
        status={
            "status": "ok",
            "latest_manager_run_at": "2026-06-30T09:55:13+00:00",
            "latest_manager_status": "ok",
            "latest_manager_mode": "llm",
            "latest_manager_error": {
                "run_at": "2026-06-29T13:09:59+00:00",
                "status": "error",
                "mode": "llm",
                "error_message": "codex native sdk timed out after 600.0s",
            },
            "latest_manager_error_recovered": True,
            "latest_unresolved_manager_error": {},
        },
        runner={},
        model="gpt-5.5",
        reasoning_effort="xhigh",
        spot_live=True,
        futures_live=True,
        upbit_live=True,
        account_risk_pct="0.25",
        max_total_exposure_usdt="0",
        max_symbol_exposure_pct="25",
        min_reward_risk="1.3",
        next_manager_run_at="2026-06-30T10:25:13+00:00",
    )

    assert "latest_manager_error" not in payload["status"]
    assert payload["status"]["latest_recovered_manager_error"] == {
        "run_at": "2026-06-29T13:09:59+00:00",
        "status": "error",
        "mode": "llm",
        "error_message": "codex native sdk timed out after 600.0s",
    }
    assert payload["status"]["latest_manager_error_recovered"] is True
    assert "latest_unresolved_manager_error" not in payload["status"]


def test_build_ops_jue_wiki_payload_exposes_stored_v3_health_and_eligibility(
    monkeypatch,
) -> None:
    from tradecraft.services.jue_wiki import JueWikiService

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("ops readiness must consume stored Wiki state only")

    monkeypatch.setattr(JueWikiService, "project_status_snapshot", fail_if_called)
    monkeypatch.setattr(JueWikiService, "repair_once", fail_if_called)
    now_iso = datetime.now(timezone.utc).isoformat()
    stored_v3 = {
        "active_read_mode": "required",
        "publication_age_sec": 901,
        "published_by_scope": {
            "kis": "snapshot:kis:stored",
            "binance": "snapshot:binance:stored",
        },
        "stale_count": 2,
        "conflicted_count": 1,
        "orphan_page_count": 1,
        "repair_backlog_count": 3,
        "last_compile_status": "error",
        "last_publish_status": "ok",
        "last_projection_status": "warning",
        "index_rebuild": {"status": "missing", "scope": "kis"},
        "by_scope": {
            "kis": {
                "snapshot_id": "snapshot:kis:stored",
                "snapshot_created_at": now_iso,
                "last_ingest_status": "ok",
                "last_compile_status": "error",
                "last_lint_status": "ok",
                "last_publish_status": "ok",
                "last_projection_status": "warning",
                "index_rebuild": {"status": "missing"},
                "stale_count": 2,
                "conflicted_count": 1,
                "orphan_page_count": 1,
                "repair_backlog_count": 3,
            },
            "binance": {
                "snapshot_id": "snapshot:binance:stored",
                "snapshot_created_at": now_iso,
                "last_ingest_status": "ok",
                "last_compile_status": "ok",
                "last_lint_status": "ok",
                "last_publish_status": "ok",
                "last_projection_status": "ok",
                "index_rebuild": {"status": "ok"},
                "stale_count": 0,
                "conflicted_count": 0,
                "orphan_page_count": 0,
                "repair_backlog_count": 0,
            },
        },
        "mode_eligibility": {
            "kis": {
                "version": "wiki_shadow_eligibility_v1",
                "venue": "kis",
                "complete_sample_count": 500,
                "required_eligible": False,
                "blockers": ["safety_gate_divergence"],
                "evaluated_at": now_iso,
                "evaluated_through": now_iso,
            },
            "binance": {
                "version": "wiki_shadow_eligibility_v1",
                "venue": "binance",
                "complete_sample_count": 520,
                "required_eligible": True,
                "blockers": [],
                "evaluated_at": now_iso,
                "evaluated_through": now_iso,
            },
        },
    }

    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={"status": "ok", "v3": stored_v3},
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["v3"] == stored_v3
    assert payload["active_read_mode"] == "required"
    assert payload["publication_age_sec"] == 901
    assert payload["comparison_count_by_venue"] == {"kis": 500, "binance": 520}
    assert payload["eligibility_by_venue"]["kis"]["required_eligible"] is False
    assert payload["eligibility_by_venue"]["binance"]["required_eligible"] is True
    assert "jue_wiki_required_knowledge_degraded" in payload["warnings"]
    assert "jue_wiki_required_kis_compile_error" in payload["blockers"]
    assert "jue_wiki_required_kis_index_missing" in payload["blockers"]
    assert (
        "jue_wiki_required_kis_eligibility_safety_gate_divergence"
        in payload["blockers"]
    )


def test_build_ops_jue_wiki_payload_keeps_prefer_degradation_advisory() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "status": "ok",
            "v3": {
                "active_read_mode": "prefer",
                "stale_count": 1,
                "conflicted_count": 0,
                "orphan_page_count": 0,
                "repair_backlog_count": 0,
                "last_compile_status": "ok",
                "index_rebuild": {"status": "ok"},
                "mode_eligibility": {
                    "kis": {
                        "complete_sample_count": 499,
                        "required_eligible": False,
                        "blockers": ["insufficient_complete_comparisons"],
                    }
                },
            },
        },
        runner={"alive": True, "status": "running"},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )

    assert payload["active_read_mode"] == "prefer"
    assert "jue_wiki_prefer_knowledge_degraded" in payload["warnings"]
    assert not any(
        signal.startswith("jue_wiki_required_") for signal in payload["blockers"]
    )


def test_stored_wiki_required_unavailable_status_is_red() -> None:
    payload = ops_payloads.build_stored_jue_wiki_readiness_status(
        {"status": "unavailable", "reason": "ops_snapshot_missing"},
        configured_read_mode="required",
        now=datetime(2026, 7, 12, 0, 5, tzinfo=timezone.utc),
    )

    assert payload["configured_read_mode"] == "required"
    assert payload["stored_read_mode"] == ""
    assert payload["read_mode_mismatch"] is True
    assert "jue_wiki_required_status_unavailable" in payload["blockers"]
    assert "jue_wiki_required_v3_missing" in payload["blockers"]


def test_stored_wiki_required_uses_scope_health_and_validates_eligibility() -> None:
    now = datetime(2026, 7, 12, 0, 5, tzinfo=timezone.utc)
    payload = ops_payloads.build_stored_jue_wiki_readiness_status(
        {
            "status": "ok",
            "v3": {
                "active_read_mode": "required",
                "by_scope": {
                    "kis": {
                        "snapshot_id": "snapshot:kis:1",
                        "snapshot_created_at": "2026-07-12T00:00:00+00:00",
                        "last_ingest_status": "ok",
                        "last_compile_status": "warning",
                        "last_lint_status": "ok",
                        "last_publish_status": "ok",
                        "last_projection_status": "warning",
                        "projection_warning_reason": "cleanup_only",
                        "index_rebuild": {"status": "ok"},
                        "stale_count": 0,
                        "conflicted_count": 0,
                        "orphan_page_count": 0,
                        "repair_backlog_count": 0,
                    }
                },
                "mode_eligibility": {
                    "kis": {
                        "version": "wiki_shadow_eligibility_v1",
                        "venue": "kis",
                        "required_eligible": True,
                        "complete_sample_count": True,
                        "blockers": [],
                        "evaluated_at": "2026-07-12T00:00:00+00:00",
                        "evaluated_through": "2026-07-11T23:59:00+00:00",
                    }
                },
            },
        },
        configured_read_mode="required",
        now=now,
    )

    assert "jue_wiki_required_kis_compile_warning" in payload["blockers"]
    assert "jue_wiki_required_binance_scope_missing" in payload["blockers"]
    assert "jue_wiki_required_kis_eligibility_sample_invalid" in payload["blockers"]
    assert "jue_wiki_required_binance_eligibility_missing" in payload["blockers"]
    assert "jue_wiki_required_kis_projection_warning" not in payload["blockers"]
