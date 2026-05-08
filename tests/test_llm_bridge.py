from __future__ import annotations

import asyncio

from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig


def test_llm_bridge_passes_timeout_to_wrapper_env(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            captured["stdin"] = input or b""
            return (b'{"content":"ok"}', b"")

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            captured["killed"] = True

    async def fake_create_subprocess_exec(*args, **kwargs):
        _ = args
        captured["env"] = kwargs.get("env")
        captured["start_new_session"] = kwargs.get("start_new_session")
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    bridge = LLMBridge(
        LLMBridgeConfig(
            command="/opt/homebrew/bin/node",
            args='["/tmp/mock-wrapper.mjs"]',
            timeout_ms=60000,
        )
    )

    result = asyncio.run(bridge.complete(payload={"messages": []}, timeout_ms=123000))
    assert bool(result.get("ok"))
    env = captured.get("env")
    assert isinstance(env, dict)
    assert env.get("LLM_BRIDGE_TIMEOUT_MS") == "123000"
    assert env.get("OPENAI_MODEL") == "gpt-5.5"
    assert captured.get("start_new_session") is True


def test_llm_bridge_timeout_kills_process_group(monkeypatch) -> None:
    captured: dict[str, object] = {"waits": 0, "signals": []}

    class _Proc:
        pid = 12345
        returncode = None

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            _ = input
            await asyncio.sleep(10)
            return (b"", b"")

        async def wait(self) -> int:
            captured["waits"] = int(captured["waits"]) + 1
            return 0

        def kill(self) -> None:
            captured["killed"] = True

    async def fake_create_subprocess_exec(*args, **kwargs):
        _ = args
        captured["start_new_session"] = kwargs.get("start_new_session")
        return _Proc()

    def fake_killpg(pid: int, sig: int) -> None:
        signals = captured["signals"]
        assert isinstance(signals, list)
        signals.append((pid, sig))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("tradecraft.services.llm_bridge.os.killpg", fake_killpg)

    bridge = LLMBridge(LLMBridgeConfig(command="/tmp/mock-wrapper", timeout_ms=1))

    result = asyncio.run(bridge.complete(payload={"messages": []}, timeout_ms=1))

    assert result["ok"] is False
    assert "timed out" in str(result["error"])
    assert captured["start_new_session"] is True
    assert captured["signals"] == [(12345, 15)]
