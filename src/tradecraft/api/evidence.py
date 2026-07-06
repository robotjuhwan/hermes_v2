from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends

from tradecraft.api.evidence_payloads import build_memory_read_only_status


@dataclass(frozen=True)
class EvidenceRouteDeps:
    require_admin_auth: Callable[..., Any]
    source_statuses: dict[str, Callable[[], Any]]
    memory_repository: Callable[[], Any]


def build_evidence_router(deps: EvidenceRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/evidence-policy/status")
    async def evidence_policy_status(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        sources: dict[str, Any] = {}
        for source_id, status_factory in deps.source_statuses.items():
            sources[source_id] = await _maybe_await(status_factory())
        return {
            "status": "ok",
            "source_count": len(sources),
            "sources": sources,
            "policy": {
                "memory_status": build_memory_read_only_status(
                    deps.memory_repository()
                ),
                "loop": (
                    "evidence -> scorecard -> policy_rule -> decision_packet -> "
                    "block_outcome"
                ),
            },
        }

    @router.get("/api/crypto/pattern-lab/status")
    async def crypto_pattern_lab_status_alias(
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        return await _source_status(deps, "crypto_pattern_lab")

    @router.get("/api/evidence-policy/context")
    async def evidence_policy_context(
        limit: int = 12,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        safe_limit = max(min(int(limit), 50), 1)
        repository = deps.memory_repository()
        return {
            "status": "ok",
            "policy_rules": repository.list_policy_rules(
                limit=safe_limit,
                active_only=True,
            ),
            "policy_scorecards": repository.list_policy_scorecards(
                limit=safe_limit
            ),
        }

    return router


async def _source_status(deps: EvidenceRouteDeps, source_id: str) -> dict[str, Any]:
    status_factory = deps.source_statuses.get(source_id)
    if status_factory is None:
        return {"status": "unavailable", "source_id": source_id}
    payload = await _maybe_await(status_factory())
    if isinstance(payload, dict):
        return payload
    return {"status": "malformed", "source_id": source_id, "value": str(payload)}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
