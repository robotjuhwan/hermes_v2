from __future__ import annotations

from tradecraft.api.ops_readiness import build_compact_ops_readiness_source


def _provider_status() -> dict[str, dict[str, object]]:
    return {
        "processes": {},
        "disk_space": {
            "status": "ok",
            "runtime_storage": {
                "status": "ok",
                "cold_archive": {"status": "ok", "corrupt_entry_ids": []},
            },
        },
        "memory": {"status": "ok"},
        "kis_block_trader": {"status": "ok"},
        "binance_block_trader": {"status": "ok"},
        "crypto_market_research": {"status": "ok"},
        "reports": {"status": "ok"},
        "market_judge": {"status": "ok"},
        "market_schedule": {"status": "ok"},
        "market_pulse": {"status": "ok"},
        "llm_usage": {"status": "ok"},
        "trading_validation": {"status": "ok"},
        "jue_wiki": {"status": "ok"},
        "crypto_alpha": {"status": "ok"},
    }


def test_cold_archive_corruption_is_an_operational_warning() -> None:
    provider = _provider_status()
    provider["disk_space"]["runtime_storage"]["cold_archive"] = {
        "status": "warning",
        "corrupt_entry_ids": ["archive-1"],
    }

    payload = build_compact_ops_readiness_source(
        provider_status=provider,
        enabled={},
        checked_at="2026-07-11T00:00:00Z",
    )

    assert payload["status"] == "yellow"
    assert "runtime_cold_archive_corrupt" in payload["warnings"]


def test_cold_archive_size_does_not_change_hot_runtime_status() -> None:
    provider = _provider_status()
    provider["disk_space"]["runtime_storage"] = {
        "status": "ok",
        "total_bytes": 3 * 1024**3,
        "cold_archive": {
            "status": "ok",
            "archive_bytes": 20 * 1024**3,
            "corrupt_entry_ids": [],
        },
    }

    payload = build_compact_ops_readiness_source(
        provider_status=provider,
        enabled={},
        checked_at="2026-07-11T00:00:00Z",
    )

    assert payload["status"] == "green"
    assert payload["warnings"] == []


def test_stale_cold_archive_verification_has_distinct_warning() -> None:
    provider = _provider_status()
    provider["disk_space"]["runtime_storage"]["cold_archive"] = {
        "status": "warning",
        "corrupt_entry_ids": [],
        "verification_snapshot": {"status": "stale"},
    }

    payload = build_compact_ops_readiness_source(
        provider_status=provider,
        enabled={},
        checked_at="2026-07-11T00:00:00Z",
    )

    assert "runtime_cold_archive_unverified" in payload["warnings"]
    assert "runtime_cold_archive_corrupt" not in payload["warnings"]


def test_compact_readiness_promotes_stored_required_wiki_blockers() -> None:
    provider = _provider_status()
    provider["jue_wiki"] = {
        "status": "degraded",
        "v3": {
            "active_read_mode": "required",
            "publication_age_sec": 901,
            "stale_count": 2,
            "conflicted_count": 1,
            "orphan_page_count": 1,
            "repair_backlog_count": 3,
            "last_compile_status": "error",
            "index_rebuild": {"status": "missing"},
        },
        "warnings": ["jue_wiki_required_knowledge_degraded"],
        "blockers": [
            "jue_wiki_required_kis_safety_gate_divergence",
            "jue_wiki_required_compilation_failed",
        ],
    }

    payload = build_compact_ops_readiness_source(
        provider_status=provider,
        enabled={"jue_wiki": True},
        checked_at="2026-07-11T00:00:00Z",
    )

    assert payload["status"] == "red"
    assert "jue_wiki_required_knowledge_degraded" in payload["warnings"]
    assert {
        "jue_wiki_required_kis_safety_gate_divergence",
        "jue_wiki_required_compilation_failed",
    }.issubset(payload["blockers"])
    assert payload["jue_wiki"]["status"]["v3"]["active_read_mode"] == (
        "required"
    )


def test_compact_required_wiki_unavailable_is_red_from_configured_mode() -> None:
    provider = _provider_status()
    provider["jue_wiki"] = {
        "status": "unavailable",
        "reason": "ops_snapshot_missing",
    }

    payload = build_compact_ops_readiness_source(
        provider_status=provider,
        enabled={"jue_wiki": True},
        checked_at="2026-07-12T00:00:00Z",
        configured_jue_wiki_read_mode="required",
    )

    assert payload["status"] == "red"
    assert "jue_wiki_required_status_unavailable" in payload["blockers"]
    assert "jue_wiki_required_v3_missing" in payload["blockers"]
    assert payload["jue_wiki"]["status"]["configured_read_mode"] == "required"
