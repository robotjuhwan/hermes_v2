from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradecraft.services.codex_contracts import CodexContractSchemaLoader
from tradecraft.services.codex_instructions import build_codex_instruction_pack
from tradecraft.services.codex_native_store import CodexNativeStore
from tradecraft.services.llm_usage import (
    LLMUsageRepository,
    estimate_tokens,
    safe_json_dumps,
)

DEFAULT_LLM_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "xhigh"
SDK_MIN_TIMEOUT_SEC = 1.0
NATIVE_SKILL_MAP = {
    "binance": ("jue-binance-trading", ".agents/skills/jue-binance-trading/SKILL.md"),
    "crypto": ("jue-binance-trading", ".agents/skills/jue-binance-trading/SKILL.md"),
    "kis": ("jue-kis-trading", ".agents/skills/jue-kis-trading/SKILL.md"),
    "memory": ("jue-memory-reflection", ".agents/skills/jue-memory-reflection/SKILL.md"),
    "research": ("jue-research", ".agents/skills/jue-research/SKILL.md"),
}
logger = logging.getLogger(__name__)


def codex_native_thread_config_kwargs(settings: Any) -> dict[str, Any]:
    return {
        "thread_mode": str(getattr(settings, "codex_native_thread_mode", "daily")),
        "thread_db_path": str(
            getattr(
                settings,
                "codex_native_thread_db_path",
                ".runtime/codex_native_threads.db",
            )
        ),
        "compact_after_turns": int(
            getattr(settings, "codex_native_compact_after_turns", 8)
        ),
        "read_turns": bool(getattr(settings, "codex_native_read_turns", False)),
        "developer_instructions_enabled": bool(
            getattr(settings, "codex_native_developer_instructions_enabled", True)
        ),
    }


def codex_native_service_config_kwargs(settings: Any) -> dict[str, Any]:
    config = codex_native_thread_config_kwargs(settings)
    return {
        "codex_native_thread_mode": config["thread_mode"],
        "codex_native_thread_db_path": config["thread_db_path"],
        "codex_native_compact_after_turns": config["compact_after_turns"],
        "codex_native_read_turns": config["read_turns"],
        "codex_native_developer_instructions_enabled": config[
            "developer_instructions_enabled"
        ],
    }


def _redact_email(value: str) -> str:
    text = str(value or "").strip()
    if "@" not in text:
        return f"{text[:2]}***" if text else ""
    name, domain = text.split("@", 1)
    return f"{name[:1]}***@{domain}"


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


def _has_renderable_messages(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if isinstance(role, str) and isinstance(content, str) and content.strip():
            return True
    return False


def _import_openai_codex() -> Any | None:
    try:
        import openai_codex  # type: ignore[import-not-found]
    except ImportError:
        return None
    return openai_codex


def _json_object_from_text(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _schema_from_example(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        properties = {
            str(key): _schema_from_example(child)
            for key, child in value.items()
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties),
        }
    if isinstance(value, list):
        item_schema = _schema_from_example(value[0]) if value else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {}

    text = str(value or "").strip()
    lower = text.lower()
    if lower in {"number", "float", "decimal"} or "0.0-1.0" in lower:
        return {"type": "number"}
    if lower in {"integer", "int"}:
        return {"type": "integer"}
    if lower in {"boolean", "bool"}:
        return {"type": "boolean"}
    if lower in {"object", "dict", "{}"}:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": [],
        }
    if lower in {"array", "list", "[]"}:
        return {"type": "array", "items": {}}
    if "|" in text and len(text) <= 120:
        choices = [item.strip() for item in text.split("|") if item.strip()]
        if len(choices) >= 2 and all(" " not in item for item in choices):
            return {"type": "string", "enum": choices}
    return {"type": "string"}


def _is_json_schema(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("type"), str)


@dataclass(slots=True)
class CodexNativeConfig:
    mode: str = "sdk"
    sdk_codex_bin: str = ""
    timeout_ms: int = 600000
    model: str = DEFAULT_LLM_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    usage_enabled: bool = True
    usage_db_path: str = ".runtime/llm_usage.db"
    usage_component: str = "unknown"
    thread_mode: str = "daily"
    thread_db_path: str = ".runtime/codex_native_threads.db"
    compact_after_turns: int = 8
    read_turns: bool = False
    developer_instructions_enabled: bool = True
    thread_lease_wait_sec: float = 120.0
    thread_lease_poll_sec: float = 5.0


class CodexNativeRuntime:
    def __init__(self, config: CodexNativeConfig) -> None:
        self.config = config

    @property
    def mode(self) -> str:
        requested = str(self.config.mode or "sdk").strip().lower()
        if requested in {"none", "off", "disabled"}:
            return "none"
        return "sdk"

    @property
    def ready(self) -> bool:
        return self.mode == "sdk"

    @property
    def resolved_model(self) -> str:
        model = str(self.config.model or "").strip()
        return model or DEFAULT_LLM_MODEL

    @property
    def resolved_reasoning_effort(self) -> str:
        effort = str(self.config.reasoning_effort or "").strip()
        return effort or DEFAULT_REASONING_EFFORT

    async def check_account(self) -> dict[str, Any]:
        module = _import_openai_codex()
        if module is None:
            return {
                "status": "error",
                "error_message": "openai-codex Python SDK is not installed",
            }
        async_codex = getattr(module, "AsyncCodex", None)
        if async_codex is None:
            return {
                "status": "error",
                "error_message": "openai-codex SDK does not expose AsyncCodex",
            }
        try:
            async with async_codex(**self._client_kwargs(module)) as codex:
                account_fn = getattr(codex, "account", None)
                if account_fn is None:
                    return {
                        "status": "error",
                        "error_message": "Codex SDK account() is unavailable",
                    }
                raw = await account_fn(refresh_token=False)
        except Exception as exc:
            CodexNativeStore(self.config.thread_db_path).record_account_check(
                status="error",
                account_label="",
                detail={},
                error_message=str(exc),
            )
            return {"status": "error", "error_message": str(exc)}

        account = dict(raw) if isinstance(raw, dict) else {"raw": str(raw)}
        if isinstance(account.get("email"), str):
            account["email"] = _redact_email(str(account["email"]))
        CodexNativeStore(self.config.thread_db_path).record_account_check(
            status="ok",
            account_label=str(account.get("email") or account.get("id") or ""),
            detail=account,
            error_message="",
        )
        return {"status": "ok", "account": account}

    async def list_models(self) -> dict[str, Any]:
        module = _import_openai_codex()
        if module is None:
            return {
                "status": "error",
                "models": [],
                "error_message": "openai-codex Python SDK is not installed",
            }
        async_codex = getattr(module, "AsyncCodex", None)
        if async_codex is None:
            return {
                "status": "error",
                "models": [],
                "error_message": "openai-codex SDK does not expose AsyncCodex",
            }
        try:
            async with async_codex(**self._client_kwargs(module)) as codex:
                models_fn = getattr(codex, "models", None)
                if models_fn is None:
                    return {
                        "status": "error",
                        "models": [],
                        "error_message": "Codex SDK models() is unavailable",
                    }
                raw = await models_fn(include_hidden=False)
        except Exception as exc:
            return {"status": "error", "models": [], "error_message": str(exc)}

        models: list[str] = []
        for row in raw if isinstance(raw, list) else []:
            if isinstance(row, dict):
                model = str(row.get("id") or row.get("name") or "").strip()
                detail = row
            else:
                model = str(
                    getattr(row, "id", "") or getattr(row, "name", "") or ""
                ).strip()
                detail = {"raw": str(row)}
            if not model:
                continue
            models.append(model)
            CodexNativeStore(self.config.thread_db_path).record_model_check(
                model=model,
                available=True,
                detail=detail,
                error_message="",
            )
        return {"status": "ok", "models": sorted(set(models))}

    async def complete(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        started_at = datetime.now(timezone.utc)
        payload = self._payload_with_reasoning(payload)
        if not self.ready:
            result = {
                "ok": False,
                "mode": "none",
                "error": "codex_native_not_configured",
            }
            await self._record_usage(
                payload=payload,
                result=None,
                status="error",
                error_message=str(result["error"]),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
            return result

        try:
            raw = await self._call_sdk(payload, timeout_ms=timeout_ms)
        except Exception as exc:
            await self._record_runtime_event(
                payload=payload,
                status="error",
                error_message=str(exc),
            )
            await self._record_usage(
                payload=payload,
                result=None,
                status="error",
                error_message=str(exc),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
            return {
                "ok": False,
                "mode": self.mode,
                "error": str(exc),
            }

        normalized = self._normalize(raw)
        normalized["ok"] = True
        normalized["mode"] = self.mode
        await self._record_native_turn(
            payload=payload,
            result=normalized,
            status="ok",
            error_message="",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
        await self._record_usage(
            payload=payload,
            result=normalized,
            status="ok",
            error_message="",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
        return normalized

    async def _record_runtime_event(
        self,
        *,
        payload: dict[str, Any],
        status: str,
        error_message: str,
    ) -> None:
        try:
            component, operation, workflow_id = self._component_operation_workflow(payload)
            await asyncio.to_thread(
                CodexNativeStore(self.config.thread_db_path).record_runtime_event,
                component=component,
                operation=operation,
                workflow_id=workflow_id,
                model=self.resolved_model,
                reasoning_effort=self.resolved_reasoning_effort,
                status=status,
                error_message=error_message,
                detail={"payload_hash": self._stable_hash(payload)},
            )
        except Exception:
            logger.exception("failed to record codex native runtime event")

    async def complete_json(
        self,
        payload: dict[str, Any],
        *,
        timeout_ms: int | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        bridge = self
        if model or reasoning_effort:
            bridge = CodexNativeRuntime(
                replace(
                    self.config,
                    model=str(model or self.config.model),
                    reasoning_effort=str(reasoning_effort or self.config.reasoning_effort),
                )
            )
        result = await bridge.complete(payload, timeout_ms=timeout_ms)
        if not bool(result.get("ok")):
            return result
        content = result.get("content")
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            return {
                "ok": False,
                "mode": result.get("mode") or bridge.mode,
                "error": "llm_json_response_not_string",
            }
        try:
            parsed = json.loads(content.strip())
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "mode": result.get("mode") or bridge.mode,
                "error": f"llm_json_error:{exc}",
                "content": content,
            }
        if not isinstance(parsed, dict):
            return {
                "ok": False,
                "mode": result.get("mode") or bridge.mode,
                "error": "llm_json_response_not_object",
                "content": content,
            }
        return parsed

    async def _record_usage(
        self,
        *,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        status: str,
        error_message: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        if not self.config.usage_enabled:
            return
        try:
            await asyncio.to_thread(
                self._record_usage_sync,
                payload=payload,
                result=result,
                status=status,
                error_message=error_message,
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception:
            logger.exception("failed to record llm usage telemetry")

    def _record_usage_sync(
        self,
        *,
        payload: dict[str, Any],
        result: dict[str, Any] | None,
        status: str,
        error_message: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        if not self.config.usage_enabled:
            return
        telemetry = (
            payload.get("telemetry")
            if isinstance(payload.get("telemetry"), dict)
            else {}
        )
        component = str(
            telemetry.get("component")
            or self.config.usage_component
            or "unknown"
        )
        operation = str(telemetry.get("operation") or payload.get("operation") or "")
        input_text = safe_json_dumps(payload) if payload else ""
        output_text = str((result or {}).get("content") or "")
        usage = (
            (result or {}).get("usage")
            if isinstance((result or {}).get("usage"), dict)
            else None
        )

        if usage:
            prompt_tokens = int(
                usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            )
            completion_tokens = int(
                usage.get("completion_tokens")
                or usage.get("output_tokens")
                or 0
            )
            total_tokens = int(
                usage.get("total_tokens") or prompt_tokens + completion_tokens
            )
            usage_source = "exact"
        elif status == "ok":
            prompt_tokens = estimate_tokens(input_text)
            completion_tokens = estimate_tokens(output_text)
            total_tokens = prompt_tokens + completion_tokens
            usage_source = "estimated"
        else:
            prompt_tokens = estimate_tokens(input_text)
            completion_tokens = 0
            total_tokens = prompt_tokens
            usage_source = "estimated" if prompt_tokens > 0 else "missing"

        repo = LLMUsageRepository(self.config.usage_db_path)
        repo.record_call(
            component=component,
            operation=operation,
            model=self.resolved_model,
            mode=self.mode,
            status=status,
            latency_ms=int((finished_at - started_at).total_seconds() * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage_source=usage_source,
            input_chars=len(input_text),
            output_chars=len(output_text),
            error_message=error_message,
            metadata={
                "timeout_ms": self.config.timeout_ms,
                "payload_keys": sorted(str(key) for key in payload.keys()),
            },
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
        )

    async def _record_native_turn(
        self,
        *,
        payload: dict[str, Any],
        result: dict[str, Any],
        status: str,
        error_message: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        native = result.get("native") if isinstance(result.get("native"), dict) else {}
        thread_key = str(native.get("thread_key") or "")
        thread_id = str(native.get("thread_id") or "")
        if not thread_key:
            thread_key = self._ephemeral_turn_key(payload, native)
            native = {**native, "thread_key": thread_key}
        if not thread_key or not thread_id:
            return
        try:
            await asyncio.to_thread(
                self._record_native_turn_sync,
                payload=payload,
                result=result,
                status=status,
                error_message=error_message,
                started_at=started_at,
                finished_at=finished_at,
                native=native,
            )
        except Exception:
            logger.exception("failed to record codex native turn telemetry")

    def _ephemeral_turn_key(
        self,
        payload: dict[str, Any],
        native: dict[str, Any],
    ) -> str:
        component = str(native.get("component") or "").strip()
        operation = str(native.get("operation") or "").strip()
        workflow_id = str(native.get("workflow_id") or "").strip()
        if not component:
            component, operation, workflow_id = self._component_operation_workflow(payload)
        scope = workflow_id or operation or "generic"
        input_hash = str(native.get("input_hash") or self._stable_hash(payload))
        return ":".join(
            part
            for part in (
                "ephemeral",
                component or "unknown",
                scope,
                input_hash[:16],
            )
            if part
        )

    def _record_native_turn_sync(
        self,
        *,
        payload: dict[str, Any],
        result: dict[str, Any],
        status: str,
        error_message: str,
        started_at: datetime,
        finished_at: datetime,
        native: dict[str, Any],
    ) -> None:
        telemetry = (
            payload.get("telemetry")
            if isinstance(payload.get("telemetry"), dict)
            else {}
        )
        component = str(
            native.get("component")
            or telemetry.get("component")
            or self.config.usage_component
            or "unknown"
        )
        operation = str(
            native.get("operation")
            or telemetry.get("operation")
            or payload.get("operation")
            or ""
        )
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
        CodexNativeStore(self.config.thread_db_path).record_turn(
            thread_key=str(native.get("thread_key") or ""),
            thread_id=str(native.get("thread_id") or ""),
            component=component,
            operation=operation,
            workflow_id=str(native.get("workflow_id") or ""),
            model=self.resolved_model,
            reasoning_effort=self.resolved_reasoning_effort,
            status=status,
            latency_ms=int((finished_at - started_at).total_seconds() * 1000),
            input_hash=str(native.get("input_hash") or ""),
            output_schema_hash=str(native.get("output_schema_hash") or ""),
            skill_refs=list(native.get("skill_refs") or []),
            usage=usage,
            error_message=error_message,
            result={"content": str(result.get("content") or "")[:4000]},
            thread_read=(
                native.get("thread_read")
                if isinstance(native.get("thread_read"), dict)
                else None
            ),
        )

    async def _call_sdk(
        self,
        payload: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> str:
        module = _import_openai_codex()
        if module is None:
            raise RuntimeError(
                "openai-codex Python SDK is not installed; run `pip install openai-codex`"
            )
        async_codex = getattr(module, "AsyncCodex", None)
        sandbox_cls = getattr(module, "Sandbox", None)
        approval_cls = getattr(module, "ApprovalMode", None)
        skill_input_cls = self._sdk_input_class(module, "SkillInput")
        text_input_cls = self._sdk_input_class(module, "TextInput")
        if async_codex is None:
            raise RuntimeError("openai-codex Python SDK does not expose AsyncCodex")

        sandbox = getattr(sandbox_cls, "read_only", None) if sandbox_cls else None
        approval_mode = (
            getattr(approval_cls, "deny_all", None) if approval_cls else None
        )
        if sandbox is None or approval_mode is None:
            raise RuntimeError(
                "openai-codex SDK missing required read_only sandbox or deny_all approval controls"
            )
        prompt = self._render_prompt(payload)
        run_input = self._sdk_run_input(
            payload,
            prompt,
            skill_input_cls=skill_input_cls,
            text_input_cls=text_input_cls,
        )
        output_schema = self._native_output_schema(payload)
        thread_key, ephemeral = self._thread_key(payload)
        component, operation, workflow_id = self._component_operation_workflow(payload)
        instruction_pack = build_codex_instruction_pack(
            self._structured_prompt_payload(payload),
            component=component,
            model=self.resolved_model,
            reasoning_effort=self.resolved_reasoning_effort,
        )
        skill_refs = self._native_skill_refs(payload)
        input_hash = self._stable_hash(payload)
        output_schema_hash = self._stable_hash(output_schema or {})
        call_started_at = datetime.now(timezone.utc)
        native_context: dict[str, Any] = {
            "thread_id": "",
            "thread_key": thread_key,
            "component": component,
            "operation": operation,
            "workflow_id": workflow_id,
            "skill_refs": skill_refs,
            "input_hash": input_hash,
            "output_schema_hash": output_schema_hash,
            "thread_read": None,
        }
        raw_timeout_ms = (
            int(timeout_ms) if timeout_ms is not None else int(self.config.timeout_ms)
        )
        timeout_sec = max(float(raw_timeout_ms) / 1000.0, SDK_MIN_TIMEOUT_SEC)
        store = self._store()
        lease_owner = f"pid:{os.getpid()}:{uuid.uuid4().hex}"
        lease_acquired = False
        if not ephemeral:
            lease_ttl_sec = max(int(timeout_sec * 2), 30)
            lease_wait_deadline = datetime.now(timezone.utc) + timedelta(
                seconds=max(float(self.config.thread_lease_wait_sec), 0.0)
            )
            while True:
                lease_acquired = await asyncio.to_thread(
                    store.acquire_thread_lease,
                    thread_key=thread_key,
                    owner=lease_owner,
                    ttl_sec=lease_ttl_sec,
                )
                if lease_acquired:
                    break
                if datetime.now(timezone.utc) >= lease_wait_deadline:
                    break
                await asyncio.sleep(max(float(self.config.thread_lease_poll_sec), 0.1))
            if not lease_acquired:
                raise RuntimeError(f"codex native thread lease unavailable: {thread_key}")

        async def run_turn() -> Any:
            async with async_codex(**self._client_kwargs(module)) as codex:
                thread_kwargs: dict[str, Any] = {
                    "model": self.resolved_model,
                    "cwd": str(Path.cwd()),
                    "ephemeral": ephemeral,
                }
                if sandbox is not None:
                    thread_kwargs["sandbox"] = sandbox
                if approval_mode is not None:
                    thread_kwargs["approval_mode"] = approval_mode
                if self.config.developer_instructions_enabled:
                    thread_kwargs["base_instructions"] = instruction_pack[
                        "base_instructions"
                    ]
                    thread_kwargs["developer_instructions"] = instruction_pack[
                        "developer_instructions"
                    ]

                stored_thread = (
                    None
                    if ephemeral
                    else await asyncio.to_thread(store.get_active_thread, thread_key)
                )
                thread = None
                if stored_thread is not None and hasattr(codex, "thread_resume"):
                    resume_kwargs: dict[str, Any] = {"model": self.resolved_model}
                    if sandbox is not None:
                        resume_kwargs["sandbox"] = sandbox
                    if approval_mode is not None:
                        resume_kwargs["approval_mode"] = approval_mode
                    if self.config.developer_instructions_enabled:
                        resume_kwargs["developer_instructions"] = instruction_pack[
                            "developer_instructions"
                        ]
                    thread = await codex.thread_resume(
                        str(stored_thread["thread_id"]),
                        **resume_kwargs,
                    )
                if thread is None:
                    thread = await codex.thread_start(**thread_kwargs)

                thread_id = str(
                    getattr(thread, "id", "")
                    or getattr(thread, "thread_id", "")
                    or (stored_thread or {}).get("thread_id")
                    or "unknown"
                )
                native_context.update({"thread_id": thread_id})
                if not ephemeral:
                    await asyncio.to_thread(
                        store.upsert_thread,
                        thread_key=thread_key,
                        thread_id=thread_id,
                        component=component,
                        workflow_id=workflow_id,
                        model=self.resolved_model,
                        reasoning_effort=self.resolved_reasoning_effort,
                        status="active",
                        metadata={
                            "operation": operation,
                            "skill_refs": skill_refs,
                            "thread_mode": self.config.thread_mode,
                        },
                    )
                    turn_count = await asyncio.to_thread(
                        store.count_turns_for_thread,
                        thread_key,
                    )
                    if (
                        int(self.config.compact_after_turns or 0) > 0
                        and turn_count >= int(self.config.compact_after_turns)
                        and hasattr(thread, "compact")
                    ):
                        await thread.compact()
                        await asyncio.to_thread(store.mark_thread_compacted, thread_key)

                run_kwargs: dict[str, Any] = {
                    "model": self.resolved_model,
                    "effort": self.resolved_reasoning_effort,
                }
                if output_schema:
                    run_kwargs["output_schema"] = output_schema
                if sandbox is not None:
                    run_kwargs["sandbox"] = sandbox
                if approval_mode is not None:
                    run_kwargs["approval_mode"] = approval_mode
                result = await thread.run(run_input, **run_kwargs)
                thread_read = None
                if self.config.read_turns and hasattr(thread, "read"):
                    thread_read = await thread.read(include_turns=True)
                native_context.update({"thread_read": thread_read})
                return {
                    "result": result,
                    "thread_id": thread_id,
                    "thread_key": thread_key,
                    "component": component,
                    "operation": operation,
                    "workflow_id": workflow_id,
                    "skill_refs": skill_refs,
                    "input_hash": input_hash,
                    "output_schema_hash": output_schema_hash,
                    "thread_read": thread_read,
                }

        try:
            try:
                run_payload = await asyncio.wait_for(run_turn(), timeout=timeout_sec)
            except asyncio.TimeoutError as exc:
                await self._record_native_turn(
                    payload=payload,
                    result={"native": native_context, "content": ""},
                    status="error",
                    error_message=f"codex native sdk timed out after {timeout_sec:.1f}s",
                    started_at=call_started_at,
                    finished_at=datetime.now(timezone.utc),
                )
                raise RuntimeError(
                    f"codex native sdk timed out after {timeout_sec:.1f}s"
                ) from exc
            except Exception as exc:
                await self._record_native_turn(
                    payload=payload,
                    result={"native": native_context, "content": ""},
                    status="error",
                    error_message=str(exc),
                    started_at=call_started_at,
                    finished_at=datetime.now(timezone.utc),
                )
                raise
        finally:
            if lease_acquired:
                await asyncio.to_thread(
                    store.release_thread_lease,
                    thread_key=thread_key,
                    owner=lease_owner,
                )

        result = run_payload["result"]
        content = str(getattr(result, "final_response", "") or "")
        usage = self._sdk_usage(getattr(result, "usage", None))
        return json.dumps(
            {
                "content": content,
                "raw": content,
                "usage": usage,
                "native": {
                    "thread_id": run_payload["thread_id"],
                    "thread_key": run_payload["thread_key"],
                    "component": run_payload["component"],
                    "operation": run_payload["operation"],
                    "workflow_id": run_payload["workflow_id"],
                    "skill_refs": run_payload["skill_refs"],
                    "input_hash": run_payload["input_hash"],
                    "output_schema_hash": run_payload["output_schema_hash"],
                    "thread_read": run_payload["thread_read"],
                },
            },
            ensure_ascii=False,
        )

    def _store(self) -> CodexNativeStore:
        return CodexNativeStore(self.config.thread_db_path)

    def _component_operation_workflow(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str, str]:
        telemetry = (
            payload.get("telemetry")
            if isinstance(payload.get("telemetry"), dict)
            else {}
        )
        structured = self._structured_prompt_payload(payload)
        workflow = structured.get("jue_workflow") if isinstance(structured, dict) else {}
        workflow_id = (
            str(workflow.get("workflow_id") or "").strip()
            if isinstance(workflow, dict)
            else ""
        )
        component = str(
            telemetry.get("component") or self.config.usage_component or "unknown"
        ).strip()
        operation = str(telemetry.get("operation") or payload.get("operation") or "").strip()
        return component or "unknown", operation, workflow_id

    def _thread_key(self, payload: dict[str, Any]) -> tuple[str, bool]:
        structured = self._structured_prompt_payload(payload)
        override = payload.get("native_thread_mode")
        if not override and isinstance(structured, dict):
            override = structured.get("native_thread_mode")
        mode = str(override or self.config.thread_mode or "daily").strip().lower()
        if mode in {"none", "off", "disabled", "ephemeral"}:
            return "", True
        explicit_key = str(payload.get("native_thread_key") or "").strip()
        if not explicit_key and isinstance(structured, dict):
            explicit_key = str(structured.get("native_thread_key") or "").strip()
        if explicit_key:
            now = datetime.now(timezone.utc)
            if "{date}" in explicit_key:
                explicit_key = explicit_key.replace("{date}", now.strftime("%Y-%m-%d"))
            return explicit_key, False
        component, _operation, workflow_id = self._component_operation_workflow(payload)
        now = datetime.now(timezone.utc)
        suffix = "persistent" if mode == "persistent" else now.strftime("%Y-%m-%d")
        key = ":".join(
            part
            for part in (component, workflow_id or "generic", suffix)
            if str(part or "").strip()
        )
        return key, False

    def _stable_hash(self, value: Any) -> str:
        return hashlib.sha256(safe_json_dumps(value).encode("utf-8")).hexdigest()

    def _sdk_codex_bin(self) -> str:
        configured = str(
            self.config.sdk_codex_bin
            or os.environ.get("TRADECRAFT_CODEX_SDK_BIN", "")
        ).strip()
        if configured:
            return configured
        app_bin = Path("/Applications/Codex.app/Contents/Resources/codex")
        if app_bin.exists():
            return str(app_bin)
        return ""

    def _client_kwargs(self, module: Any) -> dict[str, Any]:
        codex_config = getattr(module, "CodexConfig", None)
        sdk_codex_bin = self._sdk_codex_bin()
        if sdk_codex_bin and codex_config is not None:
            return {"config": codex_config(codex_bin=sdk_codex_bin)}
        return {}

    def _sdk_input_class(self, module: Any, name: str) -> Any | None:
        exported = getattr(module, name, None)
        if exported is not None:
            return exported
        try:
            inputs_module = __import__("openai_codex._inputs", fromlist=[name])
        except ImportError:
            return None
        return getattr(inputs_module, name, None)

    def _sdk_run_input(
        self,
        payload: dict[str, Any],
        prompt: str,
        *,
        skill_input_cls: Any | None,
        text_input_cls: Any | None,
    ) -> Any:
        skill_refs = self._native_skill_refs(payload)
        if not skill_refs or skill_input_cls is None or text_input_cls is None:
            return prompt
        inputs: list[Any] = []
        for row in skill_refs:
            name = str(row.get("name") or "").strip()
            path = str(row.get("path") or "").strip()
            if not name or not path:
                continue
            inputs.append(skill_input_cls(name=name, path=path))
        if not inputs:
            return prompt
        inputs.append(text_input_cls(prompt))
        return inputs

    def _native_skill_refs(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        explicit = payload.get("codex_skills") or payload.get("native_skills")
        refs = self._explicit_skill_refs(explicit)
        if refs:
            return refs

        structured = self._structured_prompt_payload(payload)
        workflow = structured.get("jue_workflow") if isinstance(structured, dict) else {}
        workflow_id = ""
        if isinstance(workflow, dict):
            workflow_id = str(workflow.get("workflow_id") or "").strip().lower()
        component = str(
            (
                payload.get("telemetry")
                if isinstance(payload.get("telemetry"), dict)
                else {}
            ).get("component")
            or self.config.usage_component
            or ""
        ).strip().lower()

        key = ""
        if "binance" in workflow_id or "crypto" in workflow_id:
            key = "binance"
        elif workflow_id in {"block_reflection", "policy_revision"}:
            key = "memory"
        elif workflow_id.startswith("kis_"):
            key = "kis"
        elif "binance" in component or "crypto" in component:
            key = "binance"
        elif "memory" in component or "reflection" in component:
            key = "memory"
        elif "research" in component or "report" in component or "strategy" in component:
            key = "research"
        elif "kis" in component or "market_judge" in component:
            key = "kis"
        if not key:
            return []
        mapped = NATIVE_SKILL_MAP.get(key)
        if not mapped:
            return []
        name, rel_path = mapped
        path = Path.cwd() / rel_path
        if not path.exists():
            return []
        return [{"name": name, "path": str(path)}]

    def _explicit_skill_refs(self, value: Any) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        if not isinstance(value, list):
            return refs
        for row in value:
            if isinstance(row, str):
                path = Path(row).expanduser()
                if not path.is_absolute():
                    path = Path.cwd() / path
                if path.exists():
                    refs.append({"name": path.parent.name or path.stem, "path": str(path)})
                continue
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("skill") or "").strip()
            raw_path = str(row.get("path") or "").strip()
            if not name or not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.exists():
                refs.append({"name": name, "path": str(path)})
        return refs

    def _structured_prompt_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("jue_workflow", "output_schema", "native_output_schema"):
            if key in payload:
                return payload
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if str(message.get("role") or "").lower() != "user":
                continue
            parsed = _json_object_from_text(message.get("content"))
            if parsed is not None:
                return parsed
        return payload

    def _native_output_schema(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        structured = self._structured_prompt_payload(payload)
        for key in ("native_output_schema",):
            value = payload.get(key)
            if isinstance(value, dict):
                return value if _is_json_schema(value) else _schema_from_example(value)
            value = structured.get(key) if isinstance(structured, dict) else None
            if isinstance(value, dict):
                return value if _is_json_schema(value) else _schema_from_example(value)

        contract_ids = self._contract_ids_from_payload(payload)
        if contract_ids:
            contract_schema = CodexContractSchemaLoader().schema_for_contract_ids(
                contract_ids
            )
            if contract_schema:
                return contract_schema

        for key in ("native_output_schema", "json_schema", "schema"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value if _is_json_schema(value) else _schema_from_example(value)
            value = structured.get(key) if isinstance(structured, dict) else None
            if isinstance(value, dict):
                return value if _is_json_schema(value) else _schema_from_example(value)

        value = payload.get("output_schema")
        if not isinstance(value, dict) and isinstance(structured, dict):
            value = structured.get("output_schema")
        if isinstance(value, dict):
            return value if _is_json_schema(value) else _schema_from_example(value)

        return None

    def _contract_ids_from_payload(self, payload: dict[str, Any]) -> list[str]:
        structured = self._structured_prompt_payload(payload)
        workflow = structured.get("jue_workflow") if isinstance(structured, dict) else {}
        contracts = workflow.get("contracts") if isinstance(workflow, dict) else None
        ids: list[str] = []
        if isinstance(contracts, list):
            for row in contracts:
                if isinstance(row, dict):
                    contract_id = str(row.get("contract_id") or "").strip()
                    if contract_id:
                        ids.append(contract_id)
        explicit = structured.get("contract_id") if isinstance(structured, dict) else ""
        if isinstance(explicit, str) and explicit.strip():
            ids.append(explicit.strip())
        return list(dict.fromkeys(ids))

    def _payload_for_native_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        if _has_renderable_messages(payload):
            return payload

        wrapped = dict(payload or {})
        system = wrapped.get("system")
        if not isinstance(system, str) or not system.strip():
            system = (
                "You are the HERMES Codex native runtime. Read the structured task payload "
                "and return a concise, schema-compliant answer."
            )
        user_content = safe_json_dumps(payload)
        wrapped["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        if "response_format" not in wrapped and (
            "output_schema" in wrapped or "schema" in wrapped
        ):
            wrapped["response_format"] = {"type": "json_object"}
        return wrapped

    def _render_prompt(self, payload: dict[str, Any]) -> str:
        wrapped = self._payload_for_native_prompt(payload)
        messages = wrapped.get("messages") if isinstance(wrapped, dict) else []
        if not isinstance(messages, list):
            messages = []
        rendered = "\n\n".join(
            f"{str(message.get('role') or '').upper()}: {message.get('content')}"
            for message in messages
            if isinstance(message, dict)
            and isinstance(message.get("role"), str)
            and isinstance(message.get("content"), str)
        ).strip()
        response_format = wrapped.get("response_format")
        response_type = (
            response_format.get("type")
            if isinstance(response_format, dict)
            else ""
        )
        if str(response_type or "").lower() == "json_object":
            rendered = (
                f"{rendered}\n\nReturn only one JSON object. "
                "Do not include markdown fences or explanatory prose."
            ).strip()
        return rendered or safe_json_dumps(payload)

    @staticmethod
    def _sdk_usage(usage: Any) -> dict[str, int] | None:
        if usage is None:
            return None
        if isinstance(usage, dict):
            return _extract_usage({"usage": usage})
        data: dict[str, Any] = {}
        for source, target in (
            ("prompt_tokens", "prompt_tokens"),
            ("input_tokens", "input_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("output_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = getattr(usage, source, None)
            if value is not None:
                data[target] = value
        if not data:
            return None
        return _extract_usage({"usage": data})

    def _payload_with_reasoning(self, payload: dict[str, Any]) -> dict[str, Any]:
        effort = self.resolved_reasoning_effort
        if not effort:
            return payload
        out = dict(payload or {})
        reasoning = out.get("reasoning") if isinstance(out.get("reasoning"), dict) else {}
        out["reasoning"] = {**reasoning, "effort": str(reasoning.get("effort") or effort)}
        out.setdefault("reasoning_effort", effort)
        return out

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
        native = (
            parsed.get("native")
            if isinstance(parsed, dict) and isinstance(parsed.get("native"), dict)
            else None
        )

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

        result: dict[str, Any] = {
            "content": str(content).strip(),
            "raw": unwrapped,
            "usage": usage,
        }
        if native is not None:
            result["native"] = native
        return result
