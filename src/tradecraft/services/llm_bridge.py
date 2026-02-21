from __future__ import annotations

import asyncio
import json
import os
import shlex
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_LLM_MODEL = "gpt-5.3-codex"


def _split_command_line(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return []


def _parse_command_args(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return _split_command_line(text)


def _strip_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text

    match = text
    if text.endswith("```"):
        lines = text.splitlines()
        if (
            len(lines) >= 2
            and lines[0].startswith("```")
            and lines[-1].strip() == "```"
        ):
            match = "\n".join(lines[1:-1]).strip()
    return match


def _extract_content(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""

    for key in ("content", "output", "answer", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value

    item = payload.get("item")
    if isinstance(item, dict):
        for key in ("text", "content", "answer"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
        message = item.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text

    return ""


def _extract_usage(payload: Any) -> dict[str, int] | None:
    if not isinstance(payload, dict):
        return None

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        item = payload.get("item")
        if isinstance(item, dict):
            usage = item.get("usage")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    total_tokens = int(usage.get("total_tokens") or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        return None
    return {
        "prompt_tokens": max(prompt_tokens, 0),
        "completion_tokens": max(completion_tokens, 0),
        "total_tokens": max(total_tokens, 0),
    }


@dataclass(slots=True)
class LLMBridgeConfig:
    command: str = ""
    args: str = ""
    url: str = ""
    token: str = ""
    timeout_ms: int = 60000
    model: str = DEFAULT_LLM_MODEL


class LLMBridge:
    def __init__(self, config: LLMBridgeConfig) -> None:
        self.config = config

    @property
    def mode(self) -> str:
        if self.config.command.strip():
            return "command"
        if self.config.url.strip():
            return "url"
        return "none"

    @property
    def ready(self) -> bool:
        return self.mode in {"command", "url"}

    @property
    def resolved_model(self) -> str:
        model = str(self.config.model or "").strip()
        return model or DEFAULT_LLM_MODEL

    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        if not self.ready:
            return {
                "ok": False,
                "mode": "none",
                "error": "llm_bridge_not_configured",
            }

        try:
            if self.mode == "command":
                raw = await self._call_command(payload, timeout_ms=timeout_ms)
            else:
                raw = await self._call_url(payload, timeout_ms=timeout_ms)
        except Exception as exc:
            return {
                "ok": False,
                "mode": self.mode,
                "error": str(exc),
            }

        normalized = self._normalize(raw)
        normalized["ok"] = True
        normalized["mode"] = self.mode
        return normalized

    async def _call_command(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> str:
        tokens = _split_command_line(self.config.command)
        if not tokens:
            raise RuntimeError("llm bridge command is empty")

        command = tokens[0]
        args = [*tokens[1:], *_parse_command_args(self.config.args)]

        raw_timeout_ms = (
            int(timeout_ms) if timeout_ms is not None else int(self.config.timeout_ms)
        )

        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "LLM_BRIDGE_TIMEOUT_MS": str(raw_timeout_ms),
            },
        )

        timeout_sec = max(float(raw_timeout_ms) / 1000.0, 1.0)
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=payload_bytes),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"llm bridge command timed out after {timeout_sec:.1f}s"
            ) from exc

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            reason = err_text or out_text or "no output"
            raise RuntimeError(
                f"llm bridge command failed (code={proc.returncode}): {reason}"
            )

        return stdout.decode("utf-8", errors="replace")

    async def _call_url(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> str:
        endpoint = self.config.url.strip()
        if not endpoint:
            raise RuntimeError("llm bridge url is empty")

        raw_timeout_ms = (
            int(timeout_ms) if timeout_ms is not None else int(self.config.timeout_ms)
        )
        timeout_sec = max(float(raw_timeout_ms) / 1000.0, 1.0)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        token = self.config.token.strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        timeout = httpx.Timeout(timeout_sec)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, json=payload, headers=headers)

        if response.status_code >= 400:
            reason = response.text.strip() or f"HTTP {response.status_code}"
            raise RuntimeError(
                f"llm bridge url request failed ({response.status_code}): {reason}"
            )

        return response.text

    def _normalize(self, raw: str) -> dict[str, Any]:
        unwrapped = _strip_code_fence(raw)
        if not unwrapped:
            return {"content": "", "raw": "", "usage": None}

        parsed: Any | None = None
        try:
            parsed = json.loads(unwrapped)
        except json.JSONDecodeError:
            parsed = None

        content = _extract_content(parsed)
        usage = _extract_usage(parsed)

        if not content:
            lines = [line.strip() for line in unwrapped.splitlines() if line.strip()]
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = _extract_content(event)
                if not usage:
                    usage = _extract_usage(event)
                if content:
                    break

        if not content:
            content = unwrapped

        return {
            "content": str(content).strip(),
            "raw": unwrapped,
            "usage": usage,
        }
