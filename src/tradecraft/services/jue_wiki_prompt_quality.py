from __future__ import annotations

from typing import Any

from tradecraft.services.jue_wiki import normalize_jue_wiki_quality_status


def _safe_count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def canonical_jue_wiki_status_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for raw_status, raw_count in value.items():
        status = normalize_jue_wiki_quality_status(raw_status)
        if not status:
            continue
        count = _safe_count(raw_count)
        if count <= 0:
            continue
        counts[status] = counts.get(status, 0) + count
    return counts


def canonical_jue_wiki_evidence_quality(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    canonical = dict(value)
    status_counts = canonical_jue_wiki_status_counts(value.get("status_counts"))
    if status_counts:
        canonical["status_counts"] = status_counts
    else:
        canonical.pop("status_counts", None)
    return canonical


def jue_wiki_quality_status_from_evidence(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    counts = canonical_jue_wiki_status_counts(value.get("status_counts"))
    for status in ("weak", "partial", "unknown", "strong"):
        if counts.get(status, 0) > 0:
            return status
    return ""
