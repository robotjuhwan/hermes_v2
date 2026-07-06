from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradecraft.api.llm import LLMRouteDeps, build_llm_router


class _FakeRuntime:
    mode = "sdk"
    resolved_model = "gpt-5.5"
    resolved_reasoning_effort = "xhigh"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        self.calls.append({"payload": payload, "timeout_ms": timeout_ms})
        return {
            "ok": True,
            "mode": "sdk",
            "content": '{"ok": true, "message": "ready"}',
        }


def _client(runtime: _FakeRuntime) -> TestClient:
    usage_summary_calls: list[tuple[str | None, str | None]] = []

    moments = iter(
        [
            datetime(2026, 6, 20, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 20, 0, 0, 1, 250000, tzinfo=timezone.utc),
        ]
    )
    app = FastAPI()
    app.include_router(
        build_llm_router(
            LLMRouteDeps(
                require_admin_auth=lambda: None,
                usage_summary=lambda trading_day, period=None: usage_summary_calls.append(
                    (trading_day, period)
                )
                or {
                    "trading_day": trading_day,
                    "period": period or "today",
                    "total": {"call_count": 1},
                },
                usage_status=lambda: {
                    "enabled": True,
                    "today": {"total": {"call_count": 1}},
                },
                runtime=lambda: runtime,
                timeout_ms=lambda: 500,
                thread_mode=lambda: "reuse",
                now=lambda: next(moments),
            )
        )
    )
    return TestClient(app)


def test_llm_usage_routes_delegate_to_payload_builders() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        summary = client.get("/api/llm/usage/summary?trading_day=2026-06-20")
        status = client.get("/api/llm/usage/status")

    assert summary.status_code == 200
    assert summary.json() == {
        "trading_day": "2026-06-20",
        "period": "today",
        "total": {"call_count": 1},
    }
    assert status.status_code == 200
    assert status.json()["enabled"] is True


def test_llm_status_legacy_alias_matches_usage_status() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        response = client.get("/api/llm/status")

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["today"]["total"]["call_count"] == 1


def test_llm_usage_summary_accepts_period_filter() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        response = client.get("/api/llm/usage/summary?period=7d")

    assert response.status_code == 200
    assert response.json() == {
        "trading_day": None,
        "period": "7d",
        "total": {"call_count": 1},
    }


def test_llm_usage_summary_exposes_operator_friendly_aliases() -> None:
    runtime = _FakeRuntime()

    app = FastAPI()
    app.include_router(
        build_llm_router(
            LLMRouteDeps(
                require_admin_auth=lambda: None,
                usage_summary=lambda trading_day, period=None: {
                    "trading_day": trading_day,
                    "period": period or "today",
                    "total": {"call_count": 2, "total_tokens": 321},
                    "by_component": [
                        {"component": "kis_block_manager", "call_count": 1},
                        {"component": "binance_block_manager", "call_count": 1},
                    ],
                },
                usage_status=lambda: {"enabled": True},
                runtime=lambda: runtime,
                timeout_ms=lambda: 500,
                thread_mode=lambda: "reuse",
                now=lambda: datetime(2026, 6, 20, tzinfo=timezone.utc),
            )
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/llm/usage/summary?period=today")

    assert response.status_code == 200
    payload = response.json()
    assert payload["components"] == payload["by_component"]
    assert payload["component_count"] == 2
    assert payload["total_requests"] == 2
    assert payload["total_tokens"] == 321


def test_llm_usage_today_alias_matches_today_summary() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        response = client.get("/api/llm/usage/today")

    assert response.status_code == 200
    assert response.json() == {
        "trading_day": None,
        "period": "today",
        "total": {"call_count": 1},
    }


def test_llm_usage_legacy_daily_alias_accepts_days_filter() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        response = client.get("/api/llm/usage/daily?days=3")

    assert response.status_code == 200
    assert response.json() == {
        "trading_day": None,
        "period": "3d",
        "total": {"call_count": 1},
    }


def test_llm_usage_legacy_root_alias_accepts_days_filter() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        response = client.get("/api/llm/usage?days=3")

    assert response.status_code == 200
    assert response.json() == {
        "trading_day": None,
        "period": "3d",
        "total": {"call_count": 1},
    }


def test_llm_probe_uses_native_runtime_and_minimum_timeout() -> None:
    runtime = _FakeRuntime()

    with _client(runtime) as client:
        response = client.post("/api/llm/probe")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ok": True,
        "native_runtime": True,
        "mode": "sdk",
        "model": "gpt-5.5",
        "reasoning_effort": "xhigh",
        "thread_mode": "reuse",
        "latency_ms": 1250,
        "timeout_ms": 1000,
        "content": '{"ok": true, "message": "ready"}',
        "error_message": "",
    }
    assert runtime.calls[0]["timeout_ms"] == 1000
    assert runtime.calls[0]["payload"]["telemetry"] == {
        "component": "llm_probe",
        "operation": "ops_probe",
    }
    assert runtime.calls[0]["payload"]["response_format"] == {"type": "json_object"}
