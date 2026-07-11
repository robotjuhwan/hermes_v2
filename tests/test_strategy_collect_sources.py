from __future__ import annotations

from tradecraft.services.strategy_collect_sources import (
    safe_runtime_cache_path,
    safe_strategy_collect_sources,
)


def test_safe_runtime_cache_path_accepts_only_runtime_cache_children(tmp_path) -> None:
    root = tmp_path
    default = ".runtime/cache/default.json"

    assert (
        safe_runtime_cache_path(".runtime/cache/custom.json", default, root=root)
        == ".runtime/cache/custom.json"
    )
    assert safe_runtime_cache_path("../outside.json", default, root=root) == default
    assert safe_runtime_cache_path("/tmp/outside.json", default, root=root) == default


def test_safe_strategy_collect_sources_normalizes_aliases_and_hosts(tmp_path) -> None:
    sources = [
        {
            "source_id": "whale",
            "url": "https://evil.example/major_stock",
            "cache_path": "../unsafe.json",
            "symbol_cache_path": ".runtime/cache/symbols.json",
            "symbol_search_url": "https://api.lefthanders-new.xyz/api/v1/assets",
        },
        {
            "source_id": "sesiban",
            "url": "https://www.sesiban.site/rankings",
            "cache_path": ".runtime/cache/sesiban.json",
        },
        {"source_id": "unknown", "url": "https://example.com"},
    ]

    normalized = safe_strategy_collect_sources(sources, root=tmp_path)

    assert [row["source_id"] for row in normalized] == [
        "whale_insight",
        "after_close_330",
    ]
    assert normalized[0]["url"] == "https://whale-insight.com/major_stock"
    assert normalized[0]["cache_path"] == ".runtime/cache/whale_insight_public_signals.json"
    assert normalized[0]["symbol_cache_path"] == ".runtime/cache/symbols.json"
    assert normalized[1]["url"] == "https://www.sesiban.site/rankings"
    assert normalized[1]["cache_path"] == ".runtime/cache/sesiban.json"


def test_safe_strategy_collect_sources_filters_requested_source_ids(tmp_path) -> None:
    sources = [
        {"source_id": "whale_insight"},
        {"source_id": "after_close_330"},
    ]

    normalized = safe_strategy_collect_sources(
        sources,
        source_ids=["sesiban"],
        root=tmp_path,
    )

    assert [row["source_id"] for row in normalized] == ["after_close_330"]
