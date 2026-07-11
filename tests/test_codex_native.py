from __future__ import annotations

import asyncio
import sqlite3
import types
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradecraft.services.codex_native import (
    CodexNativeConfig,
    CodexNativeRuntime,
    _schema_from_example,
)


def _usage_rows(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with conn:
        return conn.execute("SELECT * FROM llm_calls ORDER BY id").fetchall()


def _native_thread_db(tmp_path: Path) -> str:
    return str(tmp_path / "codex_native_threads.db")


def test_codex_native_config_default_timeout_allows_long_reasoning() -> None:
    assert CodexNativeConfig().timeout_ms == 600000


def test_codex_runtime_applies_operation_specific_model_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert "operation_model_overrides" in CodexNativeConfig.__dataclass_fields__
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            model="gpt-5.6-terra",
            reasoning_effort="high",
            operation_model_overrides={
                "weekly_review": ("gpt-5.6-sol", "max"),
            },
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        runtime.complete(
            {
                "telemetry": {
                    "component": "investment_memory",
                    "operation": "weekly_review",
                },
                "messages": [{"role": "user", "content": "Review policy."}],
            }
        )
    )

    assert result["ok"] is True
    assert captured["thread_kwargs"]["model"] == "gpt-5.6-sol"
    assert captured["run_kwargs"]["model"] == "gpt-5.6-sol"
    assert captured["run_kwargs"]["effort"] == "max"


def test_codex_native_store_records_and_resumes_thread(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    store.upsert_thread(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        thread_id="thread_123",
        component="kis_block_manager",
        workflow_id="kis_intraday_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="active",
        metadata={"scope": "daily"},
    )

    row = store.get_active_thread("kis:kis_intraday_manager:2026-06-03")
    assert row is not None
    assert row["thread_id"] == "thread_123"
    assert row["component"] == "kis_block_manager"
    assert row["workflow_id"] == "kis_intraday_manager"
    assert row["metadata"]["scope"] == "daily"

    created_at = row["created_at"]
    store.upsert_thread(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        thread_id="thread_456",
        component="kis_block_manager",
        workflow_id="kis_intraday_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="active",
        metadata={"scope": "daily", "resumed": True},
    )

    updated = store.get_active_thread("kis:kis_intraday_manager:2026-06-03")
    assert updated is not None
    assert updated["thread_id"] == "thread_456"
    assert updated["metadata"]["resumed"] is True
    assert updated["created_at"] == created_at


def test_codex_native_store_records_turn_metadata(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    run_id = store.record_turn(
        thread_key="binance:binance_cycle:2026-06-03T14",
        thread_id="thread_binance",
        component="binance_block_manager",
        operation="manager_cycle",
        workflow_id="binance_cycle",
        model="gpt-5.3-codex-spark",
        reasoning_effort="xhigh",
        status="ok",
        latency_ms=4312,
        input_hash="abc",
        output_schema_hash="def",
        skill_refs=[{"name": "jue-binance-trading", "path": "/tmp/SKILL.md"}],
        usage={"total_tokens": 1200},
        error_message="",
        result={"ok": True},
        thread_read=None,
    )

    rows = store.list_recent_turns(limit=5)
    assert rows[0]["run_id"] == run_id
    assert rows[0]["component"] == "binance_block_manager"
    assert rows[0]["usage"]["total_tokens"] == 1200
    assert rows[0]["skill_refs"][0]["name"] == "jue-binance-trading"


def test_codex_native_store_records_runtime_events(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    event_id = store.record_runtime_event(
        component="kis_block_manager",
        operation="manager_cycle",
        workflow_id="kis_intraday_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="error",
        error_message="sdk missing",
        detail={"payload_hash": "abc"},
    )

    rows = store.list_recent_runtime_events(limit=5)
    assert rows[0]["event_id"] == event_id
    assert rows[0]["component"] == "kis_block_manager"
    assert rows[0]["status"] == "error"
    assert rows[0]["detail"]["payload_hash"] == "abc"


def test_codex_native_store_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    with store._connect() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert busy_timeout == 5000
    assert synchronous == 1
    assert journal_mode == "wal"


def test_codex_native_store_serializes_non_json_values(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))
    at = datetime(2026, 6, 3, tzinfo=timezone.utc)

    store.record_turn(
        thread_key="memory:block_reflection:2026-06-03",
        thread_id="thread_memory",
        component="memory_reflection",
        operation="reflect",
        workflow_id="block_reflection",
        model="gpt-5.5",
        reasoning_effort="xhigh",
        status="ok",
        latency_ms=10,
        input_hash="abc",
        output_schema_hash="def",
        skill_refs=[{"name": "jue-memory-reflection", "path": Path("/tmp/SKILL.md")}],
        usage={"total_tokens": Decimal("12")},
        error_message="",
        result={"at": at},
        thread_read={"path": Path("/tmp/thread.json")},
    )

    row = store.list_recent_turns(limit=1)[0]
    assert row["usage"]["total_tokens"] == "12"
    assert row["result"]["at"].startswith("2026-06-03")
    assert row["thread_read"]["path"] == "/tmp/thread.json"


def test_codex_native_store_thread_lease_blocks_and_releases(tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))

    assert store.acquire_thread_lease(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        owner="owner-a",
        ttl_sec=60,
    )
    assert not store.acquire_thread_lease(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        owner="owner-b",
        ttl_sec=60,
    )

    store.release_thread_lease(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        owner="owner-a",
    )
    assert store.acquire_thread_lease(
        thread_key="kis:kis_intraday_manager:2026-06-03",
        owner="owner-b",
        ttl_sec=60,
    )


def test_codex_native_store_reclaims_dead_pid_thread_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tradecraft.services import codex_native_store
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "codex_native_threads.db"
    store = CodexNativeStore(str(db_path))
    thread_key = "binance_block_manager:binance_cycle:2026-07-01"
    assert store.acquire_thread_lease(
        thread_key=thread_key,
        owner="pid:111:old-runner",
        ttl_sec=3600,
    )
    monkeypatch.setattr(
        codex_native_store,
        "_process_is_alive",
        lambda pid: False,
    )

    assert store.acquire_thread_lease(
        thread_key=thread_key,
        owner="pid:222:new-runner",
        ttl_sec=60,
    )


def _install_fake_codex(
    monkeypatch,
    *,
    captured: dict[str, Any] | None = None,
    content: str = '{"ok":true}',
    usage: dict[str, int] | None = None,
    fail: Exception | None = None,
    run_delay_sec: float = 0.0,
) -> dict[str, Any]:
    seen = captured if captured is not None else {}

    class _Sandbox:
        read_only = "read_only"

    class _ApprovalMode:
        deny_all = "deny_all"

    class _CodexConfig:
        def __init__(self, **kwargs):
            seen["codex_config_kwargs"] = kwargs

    class _SkillInput:
        def __init__(self, *, name: str, path: str) -> None:
            self.name = name
            self.path = path

    class _TextInput:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Result:
        def __init__(self) -> None:
            self.final_response = content
            self.items: list[object] = []
            self.usage = usage

    class _Thread:
        def __init__(self, thread_id: str = "thread_new") -> None:
            self.id = thread_id

        async def run(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["run_kwargs"] = kwargs
            if run_delay_sec > 0:
                await asyncio.sleep(run_delay_sec)
            if fail is not None:
                raise fail
            return _Result()

        async def read(self, include_turns: bool = False):
            seen["read_include_turns"] = include_turns
            return {"thread_id": self.id, "turns": [] if include_turns else None}

        async def compact(self):
            seen["compacted"] = True

    class _AsyncCodex:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            seen["entered"] = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            seen["closed"] = True

        async def thread_start(self, **kwargs):
            seen["thread_kwargs"] = kwargs
            return _Thread("thread_new")

        async def thread_resume(self, thread_id: str, **kwargs):
            seen["resume_thread_id"] = thread_id
            seen["resume_kwargs"] = kwargs
            return _Thread(thread_id)

    fake_module = types.SimpleNamespace(
        AsyncCodex=_AsyncCodex,
        CodexConfig=_CodexConfig,
        Sandbox=_Sandbox,
        ApprovalMode=_ApprovalMode,
        SkillInput=_SkillInput,
        TextInput=_TextInput,
    )
    monkeypatch.setattr(
        "tradecraft.services.codex_native._import_openai_codex",
        lambda: fake_module,
    )
    return seen


def test_codex_runtime_fails_closed_without_sdk_sandbox_controls(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class _AsyncCodex:
        pass

    monkeypatch.setattr(
        "tradecraft.services.codex_native._import_openai_codex",
        lambda: types.SimpleNamespace(AsyncCodex=_AsyncCodex),
    )
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        runtime.complete(payload={"messages": [{"role": "user", "content": "hi"}]})
    )

    assert result["ok"] is False
    assert "read_only sandbox" in result["error"]
    assert "deny_all approval" in result["error"]


def test_codex_runtime_records_threadless_runtime_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    monkeypatch.setattr(
        "tradecraft.services.codex_native._import_openai_codex",
        lambda: None,
    )
    db_path = tmp_path / "native_threads.db"
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=str(db_path),
            usage_component="research_ask",
        )
    )

    result = asyncio.run(
        runtime.complete(
            payload={
                "telemetry": {"component": "research_ask", "operation": "helper_ask"},
                "messages": [{"role": "user", "content": "hi"}],
            }
        )
    )

    assert result["ok"] is False
    rows = CodexNativeStore(str(db_path)).list_recent_runtime_events(limit=1)
    assert rows[0]["component"] == "research_ask"
    assert rows[0]["operation"] == "helper_ask"
    assert "openai-codex" in rows[0]["error_message"]


def test_codex_runtime_native_sdk_mode_uses_async_codex(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(
        monkeypatch,
        usage={
            "input_tokens": 21,
            "output_tokens": 5,
            "total_tokens": 26,
        },
    )

    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            model="gpt-5.5",
            reasoning_effort="xhigh",
            sdk_codex_bin="/Applications/Codex.app/Contents/Resources/codex",
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete(
            payload={
                "model": "gpt-5.5",
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": "Return ok JSON."}],
            },
            timeout_ms=123000,
        )
    )

    assert result["ok"] is True
    assert result["mode"] == "sdk"
    assert result["content"] == '{"ok":true}'
    assert result["usage"]["total_tokens"] == 26
    assert captured["codex_config_kwargs"] == {
        "codex_bin": "/Applications/Codex.app/Contents/Resources/codex"
    }
    assert "config" in captured["client_kwargs"]
    assert captured["entered"] is True
    assert captured["closed"] is True
    assert captured["thread_kwargs"]["model"] == "gpt-5.5"
    assert captured["thread_kwargs"]["sandbox"] == "read_only"
    assert captured["thread_kwargs"]["approval_mode"] == "deny_all"
    assert captured["run_kwargs"]["model"] == "gpt-5.5"
    assert captured["run_kwargs"]["effort"] == "xhigh"
    assert captured["run_kwargs"]["sandbox"] == "read_only"
    assert captured["run_kwargs"]["approval_mode"] == "deny_all"
    assert "USER: Return ok JSON." in str(captured["prompt"])
    assert "JSON object" in str(captured["prompt"])


def test_codex_runtime_auto_mode_uses_native_sdk(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(monkeypatch)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="auto",
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete(payload={"messages": [{"role": "user", "content": "native?"}]})
    )

    assert bridge.mode == "sdk"
    assert result["mode"] == "sdk"
    assert isinstance(captured["prompt"], list)
    assert captured["prompt"][0].name == "jue-kis-trading"
    assert "USER: native?" in captured["prompt"][1].text


def test_codex_runtime_reuses_daily_thread(monkeypatch, tmp_path: Path) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="daily",
            thread_db_path=str(tmp_path / "native_threads.db"),
        )
    )

    payload = {
        "telemetry": {"component": "kis_block_manager", "operation": "manager_cycle"},
        "jue_workflow": {"workflow_id": "kis_intraday_manager"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }

    first = asyncio.run(runtime.complete(payload))
    assert first["ok"] is True
    assert captured["thread_kwargs"]["ephemeral"] is False
    assert "resume_thread_id" not in captured

    captured.clear()
    _install_fake_codex(monkeypatch, captured=captured)
    second = asyncio.run(runtime.complete(payload))
    assert second["ok"] is True
    assert captured["resume_thread_id"] == "thread_new"


def test_codex_runtime_rejects_busy_daily_thread_lease(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    captured = _install_fake_codex(monkeypatch)
    db_path = tmp_path / "native_threads.db"
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="daily",
            thread_db_path=str(db_path),
            thread_lease_wait_sec=0,
        )
    )
    payload = {
        "telemetry": {"component": "kis_block_manager", "operation": "manager_cycle"},
        "jue_workflow": {"workflow_id": "kis_intraday_manager"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }
    thread_key, ephemeral = runtime._thread_key(payload)
    assert ephemeral is False
    store = CodexNativeStore(str(db_path))
    assert store.acquire_thread_lease(
        thread_key=thread_key,
        owner="other-runner",
        ttl_sec=60,
    )

    result = asyncio.run(runtime.complete(payload))

    assert result["ok"] is False
    assert "thread lease unavailable" in result["error"]
    assert "entered" not in captured


def test_codex_runtime_waits_for_busy_daily_thread_lease(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    captured = _install_fake_codex(monkeypatch)
    db_path = tmp_path / "native_threads.db"
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="symbol_analysis",
            thread_mode="daily",
            thread_db_path=str(db_path),
            thread_lease_wait_sec=1,
            thread_lease_poll_sec=0.01,
        )
    )
    payload = {
        "native_thread_key": "symbol_analysis:033790:{date}",
        "telemetry": {"component": "symbol_analysis", "operation": "run"},
        "jue_workflow": {"workflow_id": "instant_symbol_analysis"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }
    thread_key, ephemeral = runtime._thread_key(payload)
    assert ephemeral is False
    store = CodexNativeStore(str(db_path))
    assert store.acquire_thread_lease(
        thread_key=thread_key,
        owner="other-runner",
        ttl_sec=60,
    )

    async def release_then_complete() -> dict[str, Any]:
        async def release() -> None:
            await asyncio.sleep(0.05)
            store.release_thread_lease(thread_key=thread_key, owner="other-runner")

        task = asyncio.create_task(release())
        try:
            return await runtime.complete(payload)
        finally:
            await task

    result = asyncio.run(release_then_complete())

    assert result["ok"] is True
    assert captured["thread_kwargs"]["ephemeral"] is False


def test_codex_runtime_payload_can_force_ephemeral_thread(tmp_path: Path) -> None:
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="market_judge",
            thread_mode="daily",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )
    payload = {
        "native_thread_mode": "ephemeral",
        "telemetry": {"component": "market_judge", "operation": "run_once"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }

    thread_key, ephemeral = runtime._thread_key(payload)

    assert thread_key == ""
    assert ephemeral is True


def test_codex_runtime_records_ephemeral_turn_without_active_thread(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "native_threads.db"
    _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="binance_block_manager",
            thread_mode="daily",
            thread_db_path=str(db_path),
        )
    )

    result = asyncio.run(
        runtime.complete(
            {
                "native_thread_mode": "ephemeral",
                "telemetry": {"component": "binance_block_manager"},
                "jue_workflow": {"workflow_id": "binance_cycle"},
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    store = CodexNativeStore(str(db_path))
    turns = store.list_recent_turns(limit=1)

    assert result["ok"] is True
    assert turns[0]["status"] == "ok"
    assert turns[0]["component"] == "binance_block_manager"
    assert turns[0]["workflow_id"] == "binance_cycle"
    assert turns[0]["thread_key"].startswith(
        "ephemeral:binance_block_manager:binance_cycle:"
    )
    assert store.get_active_thread(turns[0]["thread_key"]) is None


def test_codex_runtime_payload_can_override_native_thread_key(tmp_path: Path) -> None:
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="symbol_analysis",
            thread_mode="daily",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )
    payload = {
        "native_thread_key": "symbol_analysis:033790:{date}",
        "telemetry": {"component": "symbol_analysis", "operation": "run"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }

    thread_key, ephemeral = runtime._thread_key(payload)

    assert ephemeral is False
    assert thread_key.startswith("symbol_analysis:033790:")
    assert "{date}" not in thread_key


def test_codex_runtime_releases_daily_thread_lease_after_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "native_threads.db"
    _install_fake_codex(monkeypatch, run_delay_sec=1.2)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="daily",
            thread_db_path=str(db_path),
        )
    )
    payload = {
        "telemetry": {"component": "kis_block_manager", "operation": "manager_cycle"},
        "jue_workflow": {"workflow_id": "kis_intraday_manager"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }
    thread_key, ephemeral = runtime._thread_key(payload)
    assert ephemeral is False

    result = asyncio.run(runtime.complete(payload, timeout_ms=1))

    assert result["ok"] is False
    assert "timed out" in result["error"]
    store = CodexNativeStore(str(db_path))
    assert store.acquire_thread_lease(
        thread_key=thread_key,
        owner="after-timeout",
        ttl_sec=60,
    )


def test_codex_runtime_compacts_after_threshold(monkeypatch, tmp_path: Path) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="memory_reflection",
            thread_mode="daily",
            thread_db_path=str(tmp_path / "native_threads.db"),
            compact_after_turns=1,
        )
    )

    payload = {
        "telemetry": {"component": "memory_reflection", "operation": "block_reflection"},
        "jue_workflow": {"workflow_id": "block_reflection"},
        "messages": [{"role": "user", "content": "Return JSON."}],
    }

    asyncio.run(runtime.complete(payload))
    captured.clear()
    _install_fake_codex(monkeypatch, captured=captured)
    asyncio.run(runtime.complete(payload))
    assert captured["compacted"] is True


def test_codex_runtime_records_failed_native_turn(monkeypatch, tmp_path: Path) -> None:
    from tradecraft.services.codex_native_store import CodexNativeStore

    db_path = tmp_path / "native_threads.db"
    _install_fake_codex(monkeypatch, fail=RuntimeError("sdk boom"))
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="daily",
            thread_db_path=str(db_path),
        )
    )

    result = asyncio.run(
        runtime.complete(
            {
                "telemetry": {
                    "component": "kis_block_manager",
                    "operation": "manager_cycle",
                },
                "jue_workflow": {"workflow_id": "kis_intraday_manager"},
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    assert result["ok"] is False
    rows = CodexNativeStore(str(db_path)).list_recent_turns(limit=1)
    assert rows[0]["status"] == "error"
    assert rows[0]["thread_id"] == "thread_new"
    assert rows[0]["workflow_id"] == "kis_intraday_manager"
    assert "sdk boom" in rows[0]["error_message"]


def test_codex_instruction_pack_uses_developer_instructions() -> None:
    from tradecraft.services.codex_instructions import build_codex_instruction_pack

    payload = {
        "telemetry": {"component": "kis_block_manager"},
        "jue_workflow": {
            "workflow_id": "kis_intraday_manager",
            "scope": "KRX block trading",
            "language_policy": {
                "internal_reasoning_language": "English",
                "user_visible_language": "Korean",
            },
            "authority": {"llm": "block manager", "executor": "rule engine"},
            "safety_gates": ["cash_check", "kill_switch", "duplicate_order_guard"],
        },
    }

    pack = build_codex_instruction_pack(
        payload,
        component="kis_block_manager",
        model="gpt-5.5",
        reasoning_effort="xhigh",
    )

    assert "HERMES/Jue" in pack["base_instructions"]
    assert "Think in English" in pack["developer_instructions"]
    assert "Respond to the user in Korean" in pack["developer_instructions"]
    assert "cash_check" in pack["developer_instructions"]
    assert "duplicate_order_guard" in pack["developer_instructions"]


def test_codex_runtime_passes_developer_instructions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="daily",
            thread_db_path=str(tmp_path / "native_threads.db"),
        )
    )

    asyncio.run(
        runtime.complete(
            {
                "telemetry": {"component": "kis_block_manager"},
                "jue_workflow": {
                    "workflow_id": "kis_intraday_manager",
                    "scope": "KRX block trading",
                    "safety_gates": ["cash_check"],
                },
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    assert "base_instructions" in captured["thread_kwargs"]
    assert "developer_instructions" in captured["thread_kwargs"]
    assert "cash_check" in captured["thread_kwargs"]["developer_instructions"]


def test_codex_contract_schema_loads_block_action_contract() -> None:
    from tradecraft.services.codex_contracts import CodexContractSchemaLoader

    loader = CodexContractSchemaLoader()
    schema = loader.schema_for_contract_ids(["block_action_contract"])

    assert schema is not None
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "contract_id" in schema["properties"]
    assert schema["properties"]["contract_id"]["enum"] == ["block_action_contract"]
    assert "decision" in schema["properties"]
    assert set(schema["required"]) == set(schema["properties"])


def test_codex_contract_schema_avoids_oneof_for_multiple_contracts() -> None:
    from tradecraft.services.codex_contracts import CodexContractSchemaLoader

    schema = CodexContractSchemaLoader().schema_for_contract_ids(
        ["evidence_claim_contract", "thesis_update_contract"]
    )

    assert schema is not None
    assert "oneOf" not in schema["properties"]["payload"]
    assert schema["properties"]["payload"]["type"] == "object"
    assert "claim" in schema["properties"]["payload"]["properties"]
    assert "thesis_statement" in schema["properties"]["payload"]["properties"]
    assert set(schema["properties"]["payload"]["required"]) == set(
        schema["properties"]["payload"]["properties"]
    )


def test_codex_runtime_prefers_workflow_contract_schema(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    asyncio.run(
        runtime.complete(
            {
                "telemetry": {"component": "kis_block_manager"},
                "jue_workflow": {
                    "workflow_id": "kis_intraday_manager",
                    "contracts": [{"contract_id": "block_action_contract"}],
                },
                "output_schema": {"legacy": "string"},
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    schema = captured["run_kwargs"]["output_schema"]
    assert schema["properties"]["contract_id"]["enum"] == ["block_action_contract"]
    assert "legacy" not in schema["properties"]


def test_codex_runtime_prefers_explicit_native_output_schema_over_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    asyncio.run(
        runtime.complete(
            {
                "telemetry": {"component": "kis_block_manager"},
                "native_output_schema": {
                    "create_blocks": [{"symbol": "string"}],
                    "update_blocks": [],
                },
                "jue_workflow": {
                    "workflow_id": "kis_intraday_manager",
                    "contracts": [{"contract_id": "block_action_contract"}],
                },
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    schema = captured["run_kwargs"]["output_schema"]
    assert "create_blocks" in schema["properties"]
    assert "update_blocks" in schema["properties"]
    assert "contract_id" not in schema["properties"]


def test_codex_runtime_accepts_schema_alias_as_native_output_schema(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(monkeypatch)
    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    asyncio.run(
        runtime.complete(
            {
                "schema": {"answer": "string", "score": "integer"},
                "messages": [{"role": "user", "content": "Return JSON."}],
            }
        )
    )

    schema = captured["run_kwargs"]["output_schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["answer"]["type"] == "string"
    assert schema["properties"]["score"]["type"] == "integer"


def test_codex_contract_schema_rejects_retired_legacy_kis_decisions() -> None:
    from tradecraft.services.codex_contracts import CodexContractSchemaLoader
    from tradecraft.services.jue_skill_registry import JueSkillValidationError

    with pytest.raises(JueSkillValidationError, match="contract not found"):
        CodexContractSchemaLoader().schema_for_contract_ids(
            ["legacy_kis_decision_contract"]
        )


def test_codex_runtime_account_and_models(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class _AsyncCodex:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def account(self, refresh_token: bool = False):
            seen["refresh_token"] = refresh_token
            return {"email": "user@example.com", "plan": "codex"}

        async def models(self, include_hidden: bool = False):
            seen["include_hidden"] = include_hidden
            return [{"id": "gpt-5.5"}, {"id": "gpt-5.3-codex-spark"}]

    monkeypatch.setattr(
        "tradecraft.services.codex_native._import_openai_codex",
        lambda: types.SimpleNamespace(AsyncCodex=_AsyncCodex),
    )

    runtime = CodexNativeRuntime(
        CodexNativeConfig(
            usage_enabled=False,
            thread_db_path=str(tmp_path / "native_threads.db"),
        )
    )

    account = asyncio.run(runtime.check_account())
    models = asyncio.run(runtime.list_models())

    assert account["status"] == "ok"
    assert account["account"]["email"] == "u***@example.com"
    assert models["status"] == "ok"
    assert models["models"] == ["gpt-5.3-codex-spark", "gpt-5.5"]


def test_codex_runtime_wraps_raw_structured_payload_for_native_prompt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(
        monkeypatch,
        content='{"create_blocks":[]}',
    )
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="auto",
            usage_enabled=False,
            model="gpt-5.3-codex-spark",
            reasoning_effort="xhigh",
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete(
            payload={
                "task": "Manage independent Binance trading blocks",
                "output_schema": {"create_blocks": []},
            }
        )
    )

    assert result["ok"] is True
    prompt = str(captured["prompt"])
    assert "Manage independent Binance trading blocks" in prompt
    assert "Return only one JSON object" in prompt
    assert captured["run_kwargs"]["effort"] == "xhigh"
    assert captured["run_kwargs"]["output_schema"]["type"] == "object"
    assert (
        captured["run_kwargs"]["output_schema"]["properties"]["create_blocks"]["type"]
        == "array"
    )


def test_codex_schema_from_example_never_emits_untyped_empty_array_items() -> None:
    schema = _schema_from_example(
        {
            "symbol_notes": [
                {
                    "symbol": "BTCUSDT",
                    "reasons": [],
                    "risks": [],
                }
            ],
            "create_blocks": [],
        }
    )

    symbol_note = schema["properties"]["symbol_notes"]["items"]
    assert symbol_note["properties"]["reasons"]["items"]["type"] == "string"
    assert symbol_note["properties"]["risks"]["items"]["type"] == "string"
    assert schema["properties"]["create_blocks"]["items"]["type"] == "string"


def test_codex_runtime_attaches_jue_native_skill_input_for_workflow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = _install_fake_codex(monkeypatch)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            usage_enabled=False,
            usage_component="kis_block_manager",
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete(
            payload={
                "response_format": {"type": "json_object"},
                "telemetry": {"component": "kis_block_manager", "operation": "manager_run"},
                "messages": [
                    {"role": "system", "content": "Return JSON."},
                    {
                        "role": "user",
                        "content": (
                            '{"jue_workflow":{"workflow_id":"kis_intraday_manager"},'
                            '"output_schema":{"create_blocks":[{"symbol":"string"}]}}'
                        ),
                    },
                ],
            }
        )
    )

    assert result["ok"] is True
    run_input = captured["prompt"]
    assert isinstance(run_input, list)
    assert run_input[0].name == "jue-kis-trading"
    assert run_input[0].path.endswith(".agents/skills/jue-kis-trading/SKILL.md")
    assert "kis_intraday_manager" in run_input[1].text
    assert captured["run_kwargs"]["output_schema"]["properties"]["create_blocks"][
        "type"
    ] == "array"


def test_codex_runtime_complete_json_returns_parsed_native_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_fake_codex(monkeypatch, content='{"create_blocks":[]}')
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            model="gpt-5.3-codex-spark",
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete_json(
            {
                "jue_workflow": {"workflow_id": "binance_cycle"},
                "output_schema": {"create_blocks": []},
            },
            model="gpt-5.3-codex-spark",
            reasoning_effort="xhigh",
        )
    )

    assert result == {"create_blocks": []}


def test_codex_runtime_native_sdk_reports_missing_dependency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "tradecraft.services.codex_native._import_openai_codex",
        lambda: None,
    )
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            model="gpt-5.5",
            usage_enabled=False,
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(bridge.complete(payload={"messages": []}))

    assert result["ok"] is False
    assert result["mode"] == "sdk"
    assert "openai-codex" in str(result["error"])


def test_codex_runtime_auto_routes_all_components_to_sdk(tmp_path: Path) -> None:
    for component in (
        "research_pipeline",
        "kis_block_manager",
        "binance_block_manager",
        "market_judge",
        "investment_memory",
    ):
        bridge = CodexNativeRuntime(
            CodexNativeConfig(
                mode="auto",
                usage_enabled=False,
                usage_component=component,
                thread_db_path=_native_thread_db(tmp_path),
            )
        )

        assert bridge.mode == "sdk"
        assert bridge.ready is True


def test_codex_runtime_can_be_disabled() -> None:
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="none",
            usage_enabled=False,
            usage_component="research_pipeline",
            thread_db_path=".runtime/test-unused-codex-native-threads.db",
        )
    )

    assert bridge.mode == "none"
    assert bridge.ready is False


def test_codex_runtime_records_exact_usage(tmp_path, monkeypatch) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    _install_fake_codex(
        monkeypatch,
        content="ok",
        usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    )
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            usage_db_path=str(usage_db_path),
            usage_component="kis_block_manager",
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete(
            payload={
                "messages": [{"role": "user", "content": "hello"}],
                "telemetry": {"operation": "run_manager_once"},
            }
        )
    )

    assert result["usage"]["total_tokens"] == 15
    rows = _usage_rows(usage_db_path)
    assert len(rows) == 1
    assert rows[0]["component"] == "kis_block_manager"
    assert rows[0]["operation"] == "run_manager_once"
    assert rows[0]["status"] == "ok"
    assert rows[0]["usage_source"] == "exact"
    assert rows[0]["total_tokens"] == 15


def test_codex_runtime_records_estimated_usage_when_provider_usage_missing(
    tmp_path,
    monkeypatch,
) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    _install_fake_codex(monkeypatch, content="estimated output", usage=None)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            usage_db_path=str(usage_db_path),
            usage_component="investment_memory",
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(
        bridge.complete(payload={"messages": [{"role": "user", "content": "삼성전자 판단"}]})
    )

    assert result["ok"] is True
    rows = _usage_rows(usage_db_path)
    assert len(rows) == 1
    assert rows[0]["component"] == "investment_memory"
    assert rows[0]["usage_source"] == "estimated"
    assert rows[0]["prompt_tokens"] > 0
    assert rows[0]["completion_tokens"] > 0
    assert rows[0]["total_tokens"] == rows[0]["prompt_tokens"] + rows[0]["completion_tokens"]


def test_codex_runtime_records_error_usage(tmp_path, monkeypatch) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    _install_fake_codex(monkeypatch, fail=RuntimeError("native boom"))
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            usage_db_path=str(usage_db_path),
            usage_component="market_judge",
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(bridge.complete(payload={"messages": [{"content": "fail"}]}))

    assert result["ok"] is False
    rows = _usage_rows(usage_db_path)
    assert len(rows) == 1
    assert rows[0]["component"] == "market_judge"
    assert rows[0]["status"] == "error"
    assert rows[0]["usage_source"] == "estimated"
    assert rows[0]["prompt_tokens"] > 0
    assert "native boom" in rows[0]["error_message"]


def test_codex_runtime_disabled_telemetry_writes_no_usage(tmp_path, monkeypatch) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    _install_fake_codex(monkeypatch, content="ok")
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="sdk",
            usage_enabled=False,
            usage_db_path=str(usage_db_path),
            thread_mode="ephemeral",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(bridge.complete(payload={"messages": []}))

    assert result["ok"] is True
    assert not usage_db_path.exists()


def test_codex_runtime_records_not_configured_usage(tmp_path) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            mode="none",
            usage_db_path=str(usage_db_path),
            usage_component="research_ask",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )

    result = asyncio.run(bridge.complete(payload={"messages": [{"content": "hello"}]}))

    assert result["ok"] is False
    rows = _usage_rows(usage_db_path)
    assert len(rows) == 1
    assert rows[0]["component"] == "research_ask"
    assert rows[0]["mode"] == "none"
    assert rows[0]["status"] == "error"
    assert rows[0]["error_message"] == "codex_native_not_configured"


def test_codex_runtime_records_missing_usage_when_input_is_empty(tmp_path) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            usage_db_path=str(usage_db_path),
            usage_component="unknown",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )
    started_at = datetime(2026, 5, 13, tzinfo=timezone.utc)

    asyncio.run(
        bridge._record_usage(
            payload={},
            result=None,
            status="error",
            error_message="empty",
            started_at=started_at,
            finished_at=started_at,
        )
    )

    rows = _usage_rows(usage_db_path)
    assert len(rows) == 1
    assert rows[0]["usage_source"] == "missing"
    assert rows[0]["prompt_tokens"] == 0
    assert rows[0]["total_tokens"] == 0


def test_codex_runtime_telemetry_serializes_non_json_payload_values(tmp_path) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            usage_db_path=str(usage_db_path),
            usage_component="research_ask",
            thread_db_path=_native_thread_db(tmp_path),
        )
    )
    started_at = datetime(2026, 5, 13, tzinfo=timezone.utc)

    asyncio.run(
        bridge._record_usage(
            payload={
                "when": started_at,
                "path": Path("/tmp/prompt.json"),
                "amount": Decimal("1.25"),
                "bytes": b"abc",
            },
            result={"content": "ok"},
            status="ok",
            error_message="",
            started_at=started_at,
            finished_at=started_at,
        )
    )

    rows = _usage_rows(usage_db_path)
    assert len(rows) == 1
    assert rows[0]["usage_source"] == "estimated"
    assert rows[0]["input_chars"] > 0


def test_codex_runtime_records_usage_off_event_loop_thread(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_to_thread(func, /, *args, **kwargs):
        captured["func_name"] = getattr(func, "__name__", "")
        func(*args, **kwargs)

    usage_db_path = tmp_path / "llm_usage.db"
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    bridge = CodexNativeRuntime(
        CodexNativeConfig(
            usage_db_path=str(usage_db_path),
            thread_db_path=_native_thread_db(tmp_path),
        )
    )
    started_at = datetime(2026, 5, 13, tzinfo=timezone.utc)

    asyncio.run(
        bridge._record_usage(
            payload={"messages": []},
            result={"content": "ok"},
            status="ok",
            error_message="",
            started_at=started_at,
            finished_at=started_at,
        )
    )

    assert captured["func_name"] == "_record_usage_sync"
    assert len(_usage_rows(usage_db_path)) == 1
