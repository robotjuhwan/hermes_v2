from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class MemoryRouteDeps:
    require_admin_auth: Callable[..., Any]
    status: Callable[..., dict[str, Any]]
    today: Callable[..., dict[str, Any]]
    symbol_memory: Callable[[str], dict[str, Any]]
    block_memory: Callable[[str], dict[str, Any]]
    initialize: Callable[..., dict[str, Any]]
    build_context: Callable[[], Any]
    run_ritual: Callable[..., Any]
    run_update: Callable[..., Any]
    seed_current: Callable[..., dict[str, Any]]
    run_due_reflections: Callable[..., dict[str, Any]]
    latest_period_review: Callable[[str], dict[str, Any]]
    period_reviews: Callable[..., dict[str, Any]]
    run_period_review: Callable[..., Any]
    latest_historical_replay: Callable[[str], dict[str, Any]]
    historical_replays: Callable[..., dict[str, Any]]
    run_historical_replay: Callable[..., Any]
    policy_scorecards: Callable[..., dict[str, Any]]
    policy_rules: Callable[..., dict[str, Any]]
    policy_revisions: Callable[..., dict[str, Any]]
    activate_policy_revision: Callable[[str], dict[str, Any]]
    reject_policy_revision: Callable[[str], dict[str, Any]]


def build_memory_router(deps: MemoryRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/memory/status")
    async def investment_memory_status(
        scope: str = "",
        compact: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        if compact:
            return deps.status(scope=scope, compact=True)
        return _without_false_compact_marker(deps.status(scope=scope))

    @router.get("/api/memory/today")
    async def investment_memory_today(
        scope: str = "",
        compact: bool = True,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        if compact:
            return _with_compact_context_decision_skills(
                deps.today(scope=scope, compact=True)
            )
        return _without_false_compact_marker(deps.today(scope=scope))

    @router.get("/api/memory/symbols/{symbol}")
    async def investment_memory_symbol(
        symbol: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.symbol_memory(symbol)
        if result.get("status") == "invalid_symbol":
            raise HTTPException(status_code=400, detail="invalid symbol")
        return result

    @router.get("/api/memory/blocks/{block_id}")
    async def investment_memory_block(
        block_id: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.block_memory(block_id)
        if result.get("status") == "invalid_block_id":
            raise HTTPException(status_code=400, detail="invalid block id")
        return result

    @router.post("/api/memory/init")
    async def investment_memory_init(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.initialize(force=bool((payload or {}).get("force")))

    @router.post("/api/memory/rituals/run-once")
    async def investment_memory_ritual_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        return await _maybe_await(
            deps.run_ritual(
                slot=str(body.get("slot") or "pre_open"),
                context=await _maybe_await(deps.build_context()),
                send_telegram=bool(body.get("send_telegram", False)),
                force=bool(body.get("force", True)),
            )
        )

    @router.post("/api/memory/update/run-once")
    async def investment_memory_update_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _maybe_await(
            deps.run_update(
                context=await _maybe_await(deps.build_context()),
                force=bool((payload or {}).get("force", True)),
            )
        )

    @router.post("/api/memory/seed-current")
    async def investment_memory_seed_current(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.seed_current(
            context=await _maybe_await(deps.build_context()),
            force=bool((payload or {}).get("force", False)),
        )

    @router.post("/api/memory/reflections/run-due")
    async def investment_memory_reflections_run_due(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.run_due_reflections(
            context=await _maybe_await(deps.build_context()),
            force=bool((payload or {}).get("force", False)),
        )

    @router.get("/api/memory/reviews/latest")
    async def investment_memory_review_latest(
        period_type: str = "weekly",
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.latest_period_review(period_type)

    @router.get("/api/memory/reviews/history")
    async def investment_memory_review_history(
        period_type: str = "",
        limit: int = 12,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.period_reviews(
            period_type=period_type,
            limit=_bounded_limit(limit),
        )

    @router.post("/api/memory/reviews/run-once")
    async def investment_memory_review_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        return await _maybe_await(
            deps.run_period_review(
                period_type=str(body.get("period_type") or "weekly"),
                context=await _maybe_await(deps.build_context()),
                force=bool(body.get("force", True)),
            )
        )

    @router.get("/api/memory/replays/latest")
    async def investment_memory_replay_latest(
        period_type: str = "weekly",
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.latest_historical_replay(period_type)

    @router.get("/api/memory/replays/history")
    async def investment_memory_replay_history(
        period_type: str = "",
        limit: int = 12,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.historical_replays(
            period_type=period_type,
            limit=_bounded_limit(limit),
        )

    @router.post("/api/memory/replays/run-once")
    async def investment_memory_replay_run_once(
        payload: dict[str, Any] | None = None,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        body = payload or {}
        return await _maybe_await(
            deps.run_historical_replay(
                period_type=str(body.get("period_type") or "weekly"),
                context=await _maybe_await(deps.build_context()),
                force=bool(body.get("force", True)),
            )
        )

    @router.get("/api/memory/policies/scorecards")
    async def investment_memory_policy_scorecards(
        limit: int = 30,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.policy_scorecards(limit=_bounded_policy_limit(limit))

    @router.get("/api/memory/policies/rules")
    async def investment_memory_policy_rules(
        active_only: bool = False,
        limit: int = 30,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.policy_rules(
            active_only=bool(active_only),
            limit=_bounded_policy_limit(limit),
        )

    @router.get("/api/memory/policies/revisions")
    async def investment_memory_policy_revisions(
        status: str = "",
        limit: int = 30,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return deps.policy_revisions(
            status=status,
            limit=_bounded_policy_limit(limit),
        )

    @router.post("/api/memory/policies/revisions/{revision_id}/activate")
    async def investment_memory_policy_revision_activate(
        revision_id: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.activate_policy_revision(revision_id)
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="revision not found")
        return result

    @router.post("/api/memory/policies/revisions/{revision_id}/reject")
    async def investment_memory_policy_revision_reject(
        revision_id: str,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        result = deps.reject_policy_revision(revision_id)
        if result.get("status") == "missing":
            raise HTTPException(status_code=404, detail="revision not found")
        return result

    return router


def _bounded_limit(limit: int) -> int:
    return max(min(int(limit), 100), 1)


def _bounded_policy_limit(limit: int) -> int:
    return max(min(int(limit), 200), 1)


def _without_false_compact_marker(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict) and payload.get("compact") is False:
        return {key: value for key, value in payload.items() if key != "compact"}
    return payload


def _with_compact_context_decision_skills(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    decision_skills = payload.get("decision_skills")
    context_pack = payload.get("context_pack")
    if not isinstance(decision_skills, dict) or not isinstance(context_pack, dict):
        return payload
    if "decision_skills" in context_pack:
        return payload
    return {
        **payload,
        "context_pack": {
            **context_pack,
            "decision_skills": decision_skills,
        },
    }


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
