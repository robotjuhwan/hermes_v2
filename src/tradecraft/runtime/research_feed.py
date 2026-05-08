from __future__ import annotations

from typing import Any

from tradecraft.services.runtime_bridge import ResearchSnapshotReader


def read_active_research_feed(settings: Any) -> tuple[dict[str, Any] | None, str]:
    if not bool(getattr(settings, "research_enabled", False)):
        return None, "disabled"

    reader = ResearchSnapshotReader(
        str(getattr(settings, "research_state_path", "")),
        max_age_sec=int(getattr(settings, "research_max_age_sec", 3600) or 3600),
        limit=int(getattr(settings, "research_max_items", 20) or 20),
    )
    feed, status = reader.read_feed()
    if status != "ok" or not isinstance(feed, dict):
        return None, status
    return feed, "ok"
