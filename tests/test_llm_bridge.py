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
