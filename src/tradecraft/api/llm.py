from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends


@dataclass(frozen=True)
class LLMRouteDeps:
    require_admin_auth: Callable[..., Any]
    usage_summary: Callable[[str | None, str | None], Any]
    usage_status: Callable[[], Any]
    runtime: Callable[[], Any]
    timeout_ms: Callable[[], int]
    thread_mode: Callable[[], str]
    now: Callable[[], datetime]


def build_llm_router(deps: LLMRouteDeps) -> APIRouter:
    router = APIRouter()

    def period_from_days(days: int | None) -> str | None:
        if days is None:
            return None
        safe_days = max(min(int(days), 365), 1)
        return "today" if safe_days <= 1 else f"{safe_days}d"

    @router.get("/api/llm/usage/summary")
    async def llm_usage_summary(
        trading_day: str | None = None,
        period: str | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return _with_operator_summary_aliases(
            await _maybe_await(deps.usage_summary(trading_day, period))
        )

    @router.get("/api/llm/usage")
    @router.get("/api/llm/usage/daily")
    async def llm_usage_legacy_summary(
        trading_day: str | None = None,
        days: int | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return _with_operator_summary_aliases(
            await _maybe_await(deps.usage_summary(trading_day, period_from_days(days)))
        )

    @router.get("/api/llm/usage/today")
    async def llm_usage_today(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return _with_operator_summary_aliases(
            await _maybe_await(deps.usage_summary(None, "today"))
        )

    @router.get("/api/llm/status")
    @router.get("/api/llm/usage/status")
    async def llm_usage_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(deps.usage_status())

    @router.post("/api/llm/probe")
    async def llm_probe(_: Any = Depends(deps.require_admin_auth)) -> dict[str, Any]:
        runtime = deps.runtime()
        started_at = deps.now()
        timeout_ms = max(int(deps.timeout_ms()), 1000)
        payload = {
            "model": runtime.resolved_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "telemetry": {"component": "llm_probe", "operation": "ops_probe"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only one compact JSON object.",
                },
                {
                    "role": "user",
                    "content": (
                        'Return {"ok":true,"message":"ready"} if this Codex native runtime call works.'
                    ),
                },
            ],
        }
        result = await runtime.complete(payload, timeout_ms=timeout_ms)
        latency_ms = int((deps.now() - started_at).total_seconds() * 1000)
        ok = bool(result.get("ok"))
        return {
            "status": "ok" if ok else "error",
            "ok": ok,
            "native_runtime": True,
            "mode": str(result.get("mode") or runtime.mode),
            "model": runtime.resolved_model,
            "reasoning_effort": runtime.resolved_reasoning_effort,
            "thread_mode": deps.thread_mode(),
            "latency_ms": latency_ms,
            "timeout_ms": timeout_ms,
            "content": str(result.get("content") or "")[:500],
            "error_message": "" if ok else str(result.get("error") or "")[:500],
        }

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _with_operator_summary_aliases(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "unknown"}
    rows = payload.get("by_component")
    if not isinstance(rows, list):
        return payload
    enriched = dict(payload)
    enriched.setdefault("components", rows)
    enriched.setdefault("component_count", len(rows))
    total = payload.get("total") if isinstance(payload.get("total"), dict) else {}
    if "call_count" in total:
        enriched.setdefault("total_requests", total.get("call_count"))
    if "total_tokens" in total:
        enriched.setdefault("total_tokens", total.get("total_tokens"))
    return enriched
