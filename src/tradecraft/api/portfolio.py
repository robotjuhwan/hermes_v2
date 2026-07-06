from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException


@dataclass(frozen=True)
class PortfolioCoachRouteDeps:
    require_admin_auth: Callable[..., Any]
    list_advice_messages: Callable[..., list[dict[str, Any]]]
    get_advice_message: Callable[[int], dict[str, Any] | None]
    update_message_status: Callable[..., bool]
    send_message: Callable[[str], Any]


def build_portfolio_coach_router(deps: PortfolioCoachRouteDeps) -> APIRouter:
    router = APIRouter()

    @router.get("/api/portfolio-coach/review-queue")
    async def portfolio_coach_review_queue(
        status: str = "pending_review",
        limit: int = 20,
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        rows = deps.list_advice_messages(status=status, limit=limit)
        return {
            "status": "ok",
            "count": len(rows),
            "items": rows,
        }

    @router.post("/api/portfolio-coach/review-queue/{message_id}/approve")
    async def portfolio_coach_review_approve(
        message_id: int,
        payload: dict[str, Any],
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        row = deps.get_advice_message(message_id)
        if row is None:
            raise HTTPException(status_code=404, detail="message not found")

        message = str(row.get("message_md") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="empty message")

        review_note = str(payload.get("review_note") or "").strip()
        sent = await _maybe_await(deps.send_message(message))
        sent_ok = bool(sent.get("ok"))
        deps.update_message_status(
            message_id=message_id,
            status="sent" if sent_ok else "failed",
            review_note=review_note,
        )
        return {
            "status": "ok",
            "message_id": int(message_id),
            "sent": sent_ok,
        }

    @router.post("/api/portfolio-coach/review-queue/{message_id}/reject")
    async def portfolio_coach_review_reject(
        message_id: int,
        payload: dict[str, Any],
        _: Any = Depends(deps.require_admin_auth),
    ) -> dict[str, Any]:
        review_note = str(payload.get("review_note") or "").strip()
        updated = deps.update_message_status(
            message_id=message_id,
            status="rejected",
            review_note=review_note,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="message not found")
        return {
            "status": "ok",
            "message_id": int(message_id),
            "updated": True,
        }

    return router


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
