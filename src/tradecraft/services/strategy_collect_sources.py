from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


STRATEGY_COLLECT_ALLOWED_HOSTS: dict[str, set[str]] = {
    "whale_insight": {"whale-insight.com", "www.whale-insight.com"},
    "after_close_330": {
        "api.lefthanders-new.xyz",
        "www.sesiban.site",
        "sesiban.site",
    },
}

STRATEGY_COLLECT_DEFAULTS: dict[str, dict[str, str]] = {
    "whale_insight": {
        "url": "https://whale-insight.com/major_stock",
        "cache_path": ".runtime/cache/whale_insight_public_signals.json",
        "symbol_cache_path": ".runtime/cache/strategy_insight_symbol_cache.json",
        "symbol_search_url": "https://api.lefthanders-new.xyz/api/v1/assets",
    },
    "after_close_330": {
        "url": "https://api.lefthanders-new.xyz/api/v1/rankings/leading?market=KR",
        "cache_path": ".runtime/cache/sesiban_public_signals.json",
    },
}

STRATEGY_COLLECT_SOURCE_ALIASES: dict[str, str] = {
    "whale": "whale_insight",
    "whale_insight": "whale_insight",
    "sesiban": "after_close_330",
    "after_close": "after_close_330",
    "after_close_330": "after_close_330",
}


def is_allowed_collect_url(source_id: str, value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    host = (urlparse(raw).hostname or "").lower()
    return host in STRATEGY_COLLECT_ALLOWED_HOSTS.get(source_id, set())


def safe_runtime_cache_path(
    value: Any,
    default_value: str,
    *,
    root: str | Path | None = None,
) -> str:
    raw = str(value or default_value).strip() or default_value
    project_root = Path.cwd() if root is None else Path(root)
    candidate = Path(raw).expanduser()
    base = (project_root / ".runtime" / "cache").resolve()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (project_root / candidate).resolve()
    )
    try:
        resolved.relative_to(base)
    except ValueError:
        return default_value
    return raw


def safe_strategy_collect_sources(
    configured_sources: list[dict[str, Any]],
    source_ids: list[str] | None = None,
    *,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    wanted = {
        STRATEGY_COLLECT_SOURCE_ALIASES.get(str(item or "").strip().lower(), "")
        for item in (source_ids or [])
        if str(item or "").strip()
    }
    wanted.discard("")
    safe_sources: list[dict[str, Any]] = []
    for source in configured_sources:
        source_id = STRATEGY_COLLECT_SOURCE_ALIASES.get(
            str(source.get("source_id") or "").strip().lower(),
            "",
        )
        if source_id not in STRATEGY_COLLECT_DEFAULTS:
            continue
        if wanted and source_id not in wanted:
            continue
        defaults = STRATEGY_COLLECT_DEFAULTS[source_id]
        row = dict(source)
        row["source_id"] = source_id
        row["url"] = (
            str(source.get("url")).strip()
            if is_allowed_collect_url(source_id, source.get("url"))
            else defaults["url"]
        )
        row["cache_path"] = safe_runtime_cache_path(
            source.get("cache_path"),
            defaults["cache_path"],
            root=root,
        )
        if source_id == "whale_insight":
            row["symbol_cache_path"] = safe_runtime_cache_path(
                source.get("symbol_cache_path"),
                defaults["symbol_cache_path"],
                root=root,
            )
            row["symbol_search_url"] = (
                str(source.get("symbol_search_url")).strip()
                if is_allowed_collect_url(
                    "after_close_330",
                    source.get("symbol_search_url"),
                )
                else defaults["symbol_search_url"]
            )
        safe_sources.append(row)
    return safe_sources
