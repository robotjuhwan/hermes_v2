from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class WikiRepairHealthPolicy:
    overdue_sec: int = 86_400
    stall_sec: int = 21_600
    growth_window_sec: int = 86_400
    growth_warn_count: int = 25

    def __post_init__(self) -> None:
        object.__setattr__(self, "overdue_sec", max(int(self.overdue_sec), 1))
        object.__setattr__(self, "stall_sec", max(int(self.stall_sec), 1))
        object.__setattr__(
            self,
            "growth_window_sec",
            max(int(self.growth_window_sec), 1),
        )
        object.__setattr__(
            self,
            "growth_warn_count",
            max(int(self.growth_warn_count), 1),
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def age_seconds(value: Any, now: datetime) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(int((_as_utc(now) - _as_utc(parsed)).total_seconds()), 0)


def _safe_count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def evaluate_repair_queue_health(
    queue: dict[str, Any],
    *,
    policy: WikiRepairHealthPolicy,
    now: datetime,
) -> dict[str, Any]:
    open_count = _safe_count(queue.get("open_count"))
    oldest_age = age_seconds(queue.get("oldest_open_at"), now)
    progress_age = age_seconds(queue.get("last_resolved_at"), now)
    opened = _safe_count(queue.get("opened_in_window"))
    resolved = _safe_count(queue.get("resolved_in_window"))
    net_growth = opened - resolved

    warnings: list[str] = []
    stalled = open_count > 0 and (
        progress_age is None or progress_age > policy.stall_sec
    )
    if (
        stalled
        and oldest_age is not None
        and oldest_age > policy.overdue_sec
    ):
        warnings.append("jue_wiki_repair_queue_overdue")
    if stalled:
        warnings.append("jue_wiki_repair_queue_stalled")
    if open_count and net_growth >= policy.growth_warn_count:
        warnings.append("jue_wiki_repair_queue_growing")

    return {
        "status": "warning" if warnings else "progressing" if open_count else "idle",
        "warning_signals": warnings,
        "advisory_signals": ["jue_wiki_repair_queue_open"] if open_count else [],
        "oldest_open_age_sec": oldest_age,
        "progress_age_sec": progress_age,
        "net_growth_in_window": net_growth,
        "growth_window_sec": policy.growth_window_sec,
    }
