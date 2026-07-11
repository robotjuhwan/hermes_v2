from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from tradecraft.runtime import jue_wiki_runner
from tradecraft.runtime.jue_wiki_runner import run_v3_scope
from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_compiler import (
    JueWikiPublisherV1,
    WikiPublicationError,
)
from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    JueWikiPageV3,
    WikiClaimV3,
    WikiSnapshotV1,
)
from tradecraft.services.jue_wiki_repository import JueWikiRepository
from tradecraft.services.jue_wiki_projection import WikiProjectionError


def _service(tmp_path: Path) -> JueWikiService:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "pages",
            db_path=tmp_path / "wiki.db",
        )
    )
    service.initialize()
    service.repository().initialize()
    return service


def _empty_snapshot(snapshot_id: str) -> WikiSnapshotV1:
    return WikiSnapshotV1(
        snapshot_id=snapshot_id,
        scope="kis",
        candidate_artifact_ids=(),
        pages=(),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )


def _artifact(scope: str = "kis") -> CandidateArtifactV1:
    evidence = EvidenceRefV1(
        evidence_id="source:1",
        source_type="source",
        source_id="1",
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
        source_path="/read-only/source.db",
    )
    claim = WikiClaimV3(
        claim_id="claim:1",
        claim_type="fact",
        text="verified source fact",
        status="verified",
        scope=scope,
        evidence=(evidence,),
        symbols=("005930",),
        confidence=0.9,
        provenance_id="artifact:1",
    )
    return CandidateArtifactV1(
        artifact_id="artifact:1",
        scope=scope,
        extractor_version="extractor:v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(claim,),
        created_at="2026-07-11T00:00:00+00:00",
    )


class _FailingPublisher:
    def compile_snapshot(
        self,
        *,
        scope: str,
        artifact_ids: tuple[str, ...],
    ) -> WikiSnapshotV1:
        invalid_page = JueWikiPageV3(
            page_id="binance.core.invalid",
            page_type="core",
            scope="binance",
            title="Invalid",
            summary="cross-scope page",
            claims=(),
            relationships=(),
            status="draft",
            schema_version="jue_wiki_page_v3",
            compiler_version="wiki_compiler_v1",
        )
        return replace(
            _empty_snapshot("snapshot:kis:lint-error"),
            pages=(invalid_page,),
        )

    def publish_snapshot(self, snapshot: WikiSnapshotV1) -> WikiSnapshotV1:
        raise AssertionError("lint errors must not publish")


class _PublishingPublisher:
    def __init__(
        self,
        repository: JueWikiRepository,
        snapshot: WikiSnapshotV1,
    ) -> None:
        self.repository = repository
        self.snapshot = snapshot

    def compile_snapshot(
        self,
        *,
        scope: str,
        artifact_ids: tuple[str, ...],
    ) -> WikiSnapshotV1:
        return self.snapshot

    def publish_snapshot(self, snapshot: WikiSnapshotV1) -> WikiSnapshotV1:
        assert snapshot == self.snapshot
        self.repository.publish_snapshot(self.snapshot)
        return self.snapshot


def test_v3_lint_failure_preserves_previous_snapshot(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.repository().publish_snapshot(_empty_snapshot("snapshot:kis:previous"))
    previous = service.repository().current_snapshot("kis")

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=(),
        publisher=_FailingPublisher(),
        projection_writer=None,
    )

    assert result["status"] == "error"
    assert service.repository().current_snapshot("kis") == previous


def test_v3_compile_failure_skips_lint_publish_and_projection(tmp_path: Path) -> None:
    service = _service(tmp_path)

    class Publisher:
        def compile_snapshot(
            self,
            *,
            scope: str,
            artifact_ids: tuple[str, ...],
        ) -> WikiSnapshotV1:
            raise ValueError("compiler_failed")

        def publish_snapshot(self, snapshot: WikiSnapshotV1) -> WikiSnapshotV1:
            raise AssertionError("compile failure must not publish")

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=(),
        publisher=Publisher(),
        projection_writer=None,
    )

    assert result["status"] == "error"
    assert result["v3_compile"]["status"] == "error"
    assert result["v3_compile"]["error_message"] == "compiler_failed"
    assert result["v3_compile"]["elapsed_sec"] >= 0
    assert result["v3_lint"]["status"] == "skipped"
    assert result["v3_publish"]["status"] == "skipped"
    assert result["v3_projection"]["status"] == "skipped"


def test_runner_status_projection_is_written_after_publish(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = _empty_snapshot("snapshot:kis:new")

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=(),
        publisher=_PublishingPublisher(service.repository(), snapshot),
        projection_writer=None,
    )
    status = service.project_status_snapshot()

    assert result["status"] == "ok"
    assert status["v3"]["published_by_scope"]["kis"] == snapshot.snapshot_id


def test_v3_scope_registers_evidence_before_candidate_and_reports_all_steps(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    artifact = _artifact()
    events: list[str] = []
    repository = service.repository()

    class Adapter:
        def collect(
            self,
            symbols: tuple[str, ...],
            observed_at: str,
        ) -> tuple[CandidateArtifactV1, ...]:
            assert symbols == ()
            assert observed_at.endswith("+00:00")
            events.append("collect")
            return (artifact,)

    class Publisher:
        def compile_snapshot(
            self,
            *,
            scope: str,
            artifact_ids: tuple[str, ...],
        ) -> WikiSnapshotV1:
            assert repository.evidence_refs()["source:1"] == artifact.source_refs[0]
            assert repository.candidate_artifacts(artifact_ids) == (artifact,)
            events.append("compile")
            snapshot = replace(
                _empty_snapshot("snapshot:kis:artifact"),
                candidate_artifact_ids=artifact_ids,
            )
            return snapshot

        def publish_snapshot(self, snapshot: WikiSnapshotV1) -> WikiSnapshotV1:
            events.append("publish")
            repository.publish_snapshot(snapshot)
            return snapshot

    class ProjectionWriter:
        def project(self, snapshot: WikiSnapshotV1) -> SimpleNamespace:
            assert repository.current_snapshot("kis") == snapshot
            events.append("project")
            return SimpleNamespace(cleanup_warnings=("old_generation_cleanup_failed",))

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=(Adapter(),),
        publisher=Publisher(),
        projection_writer=ProjectionWriter(),
    )

    assert events == ["collect", "compile", "publish", "project"]
    assert result["candidate_count"] == 1
    assert result["snapshot_count"] == 1
    assert result["snapshot_id"] == "snapshot:kis:artifact"
    assert result["warning_count"] == 1
    assert result["elapsed_sec"] >= 0
    assert result["cleanup_warnings"] == ["old_generation_cleanup_failed"]
    for step_name in (
        "v3_ingest",
        "v3_compile",
        "v3_lint",
        "v3_publish",
        "v3_projection",
    ):
        assert {
            "scope",
            "candidate_count",
            "snapshot_id",
            "snapshot_count",
            "page_count",
            "warning_count",
            "elapsed_sec",
            "error_message",
        } <= result[step_name].keys()


def test_v3_lint_failure_never_projects(tmp_path: Path) -> None:
    service = _service(tmp_path)

    class ProjectionWriter:
        def project(self, snapshot: WikiSnapshotV1) -> None:
            raise AssertionError("lint failure must not project")

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=(),
        publisher=_FailingPublisher(),
        projection_writer=ProjectionWriter(),
    )

    assert result["v3_compile"]["status"] == "ok"
    assert result["v3_lint"]["status"] == "error"
    assert result["v3_publish"]["status"] == "skipped"
    assert result["v3_projection"]["status"] == "skipped"


def test_v3_repository_publish_failure_is_attributed_to_publish_stage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    class Publisher:
        def compile_snapshot(
            self,
            *,
            scope: str,
            artifact_ids: tuple[str, ...],
        ) -> WikiSnapshotV1:
            return _empty_snapshot("snapshot:kis:publish-error")

        def publish_snapshot(self, snapshot: WikiSnapshotV1) -> WikiSnapshotV1:
            raise WikiPublicationError(
                "wiki_snapshot_publish_failed",
                stage="publish",
            )

    class ProjectionWriter:
        def project(self, snapshot: WikiSnapshotV1) -> None:
            raise AssertionError("publish failure must not project")

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=(),
        publisher=Publisher(),
        projection_writer=ProjectionWriter(),
    )

    assert result["status"] == "error"
    assert result["v3_compile"]["status"] == "ok"
    assert result["v3_compile"]["elapsed_sec"] >= 0
    assert result["v3_lint"]["status"] == "ok"
    assert result["v3_lint"]["elapsed_sec"] >= 0
    assert result["v3_publish"]["status"] == "error"
    assert result["v3_publish"]["elapsed_sec"] >= 0
    assert result["v3_publish"]["error_message"] == "wiki_snapshot_publish_failed"
    assert result["v3_projection"]["status"] == "skipped"
    assert service.repository().current_snapshot("kis") is None


def test_v3_status_counts_current_claims_and_stores_runner_outcomes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    evidence = _artifact().source_refs
    stale = WikiClaimV3(
        claim_id="claim:stale",
        claim_type="fact",
        text="stale fact",
        status="stale",
        scope="kis",
        evidence=evidence,
    )
    conflicted = WikiClaimV3(
        claim_id="claim:conflicted",
        claim_type="interpretation",
        text="conflicted interpretation",
        status="conflicted",
        scope="kis",
        evidence=evidence,
    )
    pages = (
        JueWikiPageV3(
            page_id="kis.symbol.005930",
            page_type="symbol",
            scope="kis",
            title="005930",
            summary="status counts",
            claims=(stale, conflicted),
            relationships=(),
            status="conflicted",
            schema_version="jue_wiki_page_v3",
            compiler_version="wiki_compiler_v1",
        ),
        JueWikiPageV3(
            page_id="kis.core.orphan",
            page_type="core",
            scope="kis",
            title="Orphan",
            summary="no claims",
            claims=(),
            relationships=(),
            status="draft",
            schema_version="jue_wiki_page_v3",
            compiler_version="wiki_compiler_v1",
        ),
    )
    service.repository().publish_snapshot(
        replace(
            _empty_snapshot("snapshot:kis:status"),
            pages=tuple(sorted(pages, key=lambda page: page.page_id)),
        )
    )
    run_result = {
        "v3_compile": {"status": "ok"},
        "v3_publish": {"status": "ok"},
        "v3_projection": {"status": "warning"},
        "cleanup_warnings": ["old_generation_cleanup_failed"],
    }

    status = service.project_status_snapshot(v3_run_results={"kis": run_result})
    stored = service.status()
    refreshed = service.project_status_snapshot()

    assert stored == status
    assert refreshed["v3"] == status["v3"]
    expected_v3 = {
        "published_by_scope": {
            "kis": "snapshot:kis:status",
            "binance": "",
        },
        "claim_status_counts": {"conflicted": 1, "stale": 1},
        "stale_count": 1,
        "conflicted_count": 1,
        "orphan_page_count": 1,
        "repair_backlog_count": 0,
        "last_ingest_status": "ok",
        "last_compile_status": "ok",
        "last_lint_status": "ok",
        "last_publish_status": "ok",
        "last_projection_status": "warning",
        "cleanup_warnings": ["old_generation_cleanup_failed"],
        "cleanup_warnings_by_scope": {
            "kis": ["old_generation_cleanup_failed"],
            "binance": [],
        },
        "mode_eligibility": {"kis": {}, "binance": {}},
    }
    assert {
        key: status["v3"][key]
        for key in expected_v3
    } == expected_v3
    assert status["v3"]["by_scope"]["kis"]["snapshot_id"] == (
        "snapshot:kis:status"
    )
    assert status["v3"]["by_scope"]["kis"]["index_rebuild"] == {
        "status": "ok"
    }


def test_v3_status_persists_runner_read_mode_and_signed_eligibility(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    eligibility = {
        "kis": {
            "venue": "kis",
            "required_eligible": True,
            "complete_sample_count": 500,
            "blockers": [],
            "reason": "required_acceptance_gates_passed",
        },
        "binance": {
            "venue": "binance",
            "required_eligible": False,
            "complete_sample_count": 420,
            "blockers": ["insufficient_complete_comparisons"],
            "reason": "insufficient_complete_comparisons",
        },
    }

    projected = service.project_status_snapshot(
        active_read_mode="required",
        mode_eligibility=eligibility,
    )
    stored = service.status()
    refreshed = service.project_status_snapshot()

    assert projected["v3"]["active_read_mode"] == "required"
    assert projected["v3"]["mode_eligibility"] == eligibility
    assert stored["v3"]["active_read_mode"] == "required"
    assert refreshed["v3"]["mode_eligibility"] == eligibility


def test_v3_status_merge_retains_skipped_and_replaces_completed_stages(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.repository().publish_snapshot(_empty_snapshot("snapshot:kis:merge"))
    initial = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_compile": {"status": "error"},
                "v3_publish": {"status": "warning"},
                "v3_projection": {"status": "warning"},
                "cleanup_warnings": ["old_generation_cleanup_failed"],
            }
        }
    )["v3"]
    skipped = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_compile": {"status": "skipped"},
                "v3_publish": {"status": "skipped"},
                "v3_projection": {"status": "skipped"},
                "cleanup_warnings": [],
            },
            "binance": {},
        }
    )["v3"]

    assert skipped["last_compile_status"] == initial["last_compile_status"]
    assert skipped["last_publish_status"] == initial["last_publish_status"]
    assert skipped["last_projection_status"] == initial["last_projection_status"]
    assert skipped["cleanup_warnings"] == initial["cleanup_warnings"]

    completed = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_compile": {"status": "ok"},
                "v3_publish": {"status": "ok"},
                "v3_projection": {"status": "ok"},
                "cleanup_warnings": [],
            }
        }
    )["v3"]

    assert completed["last_compile_status"] == "ok"
    assert completed["last_publish_status"] == "ok"
    assert completed["last_projection_status"] == "ok"
    assert completed["cleanup_warnings"] == []

    warning = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_compile": {"status": "ok"},
                "v3_publish": {"status": "ok"},
                "v3_projection": {"status": "warning"},
                "cleanup_warnings": ["replacement_cleanup_warning"],
            }
        }
    )["v3"]

    assert warning["last_projection_status"] == "warning"
    assert warning["cleanup_warnings"] == ["replacement_cleanup_warning"]


def test_v3_cleanup_warnings_merge_per_scope_and_migrate_flat_snapshot(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_compile": {"status": "ok"},
                "v3_publish": {"status": "ok"},
                "v3_projection": {"status": "warning"},
                "cleanup_warnings": ["kis_old_warning"],
            },
            "binance": {
                "v3_compile": {"status": "ok"},
                "v3_publish": {"status": "ok"},
                "v3_projection": {"status": "warning"},
                "cleanup_warnings": ["binance_old_warning"],
            },
        }
    )
    partial = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_projection": {"status": "ok"},
                "cleanup_warnings": [],
            },
            "binance": {
                "v3_projection": {"status": "error"},
                "cleanup_warnings": [],
            },
        }
    )["v3"]

    assert partial["cleanup_warnings_by_scope"] == {
        "kis": [],
        "binance": ["binance_old_warning"],
    }
    assert partial["cleanup_warnings"] == ["binance_old_warning"]

    replaced = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_projection": {"status": "warning"},
                "cleanup_warnings": ["kis_new_warning"],
            },
            "binance": {
                "v3_projection": {"status": "ok"},
                "cleanup_warnings": [],
            },
        }
    )["v3"]

    assert replaced["cleanup_warnings_by_scope"] == {
        "kis": ["kis_new_warning"],
        "binance": [],
    }
    assert replaced["cleanup_warnings"] == ["kis_new_warning"]

    with sqlite3.connect(service.config.db_path) as conn:
        row = conn.execute(
            """
            SELECT payload_json
            FROM wiki_ops_section_snapshots
            WHERE section = ?
            """,
            (service.OPS_SNAPSHOT_SECTION,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["v3"].pop("cleanup_warnings_by_scope", None)
        payload["v3"]["cleanup_warnings"] = ["legacy_flat_warning"]
        conn.execute(
            """
            UPDATE wiki_ops_section_snapshots
            SET payload_json = ?
            WHERE section = ?
            """,
            (json.dumps(payload), service.OPS_SNAPSHOT_SECTION),
        )

    migrated_partial = service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_projection": {"status": "ok"},
                "cleanup_warnings": [],
            },
            "binance": {
                "v3_projection": {"status": "skipped"},
                "cleanup_warnings": [],
            },
        }
    )["v3"]

    assert migrated_partial["cleanup_warnings_by_scope"] == {
        "kis": [],
        "binance": ["legacy_flat_warning"],
    }
    assert migrated_partial["cleanup_warnings"] == ["legacy_flat_warning"]

    cleared = service.project_status_snapshot(
        v3_run_results={
            scope: {
                "v3_projection": {"status": "ok"},
                "cleanup_warnings": [],
            }
            for scope in ("kis", "binance")
        }
    )["v3"]
    assert cleared["cleanup_warnings_by_scope"] == {"kis": [], "binance": []}
    assert cleared["cleanup_warnings"] == []


def test_run_once_isolates_v3_scopes_and_keeps_legacy_steps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []

    def fake_dependencies(
        _service: JueWikiService,
        scope: str,
    ) -> tuple[tuple[object, ...], object, object | None]:
        calls.append(f"dependencies:{scope}")
        return (), object(), None

    def fake_scope(
        service: JueWikiService,
        scope: str,
        adapters: object,
        publisher: object,
        projection_writer: object | None,
    ) -> dict[str, object]:
        calls.append(f"v3:{scope}")
        status = "error" if scope == "kis" else "ok"
        step = {"status": status}
        return {
            "status": status,
            "v3_ingest": {"status": "ok"},
            "v3_compile": step,
            "v3_lint": step,
            "v3_publish": step,
            "v3_projection": step,
            "cleanup_warnings": [],
        }

    monkeypatch.setattr(
        jue_wiki_runner,
        "_build_v3_scope_dependencies",
        fake_dependencies,
    )
    monkeypatch.setattr(jue_wiki_runner, "run_v3_scope", fake_scope)

    result = jue_wiki_runner.run_once(
        service=service,
        state_path=tmp_path / "runner-state.json",
        investment_memory_db_path=tmp_path / "investment-memory.db",
        performance_db_path=tmp_path / "performance.db",
        market_judgment_db_path=tmp_path / "market-judgment.db",
    )

    assert calls == [
        "dependencies:kis",
        "v3:kis",
        "dependencies:binance",
        "v3:binance",
    ]
    assert result["v3"]["kis"]["status"] == "error"
    assert result["v3"]["binance"]["status"] == "ok"
    assert result["rebuild"]["status"] == "ok"
    assert result["lint"]["status"] == "ok"
    assert "repair" in result
    assert "playbooks" in result
    assert "performance" in result
    assert "application" in result
    assert result["ops_snapshot"]["v3"]["last_compile_status"] == "error"


def test_default_binance_source_is_read_only_and_projection_is_scope_owned(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "crypto-source.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_symbol_notes (
                symbol TEXT PRIMARY KEY,
                stance TEXT NOT NULL,
                horizon TEXT NOT NULL,
                confidence REAL NOT NULL,
                summary_md TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                risks_json TEXT NOT NULL,
                triggers_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO crypto_symbol_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"BTC{index:03d}USDT",
                    "bullish",
                    "swing",
                    0.72,
                    f"Momentum is constructive for {index}",
                    '["breakout"]',
                    '["funding crowding"]',
                    '["daily close"]',
                    "2026-07-10T12:00:00+00:00",
                )
                for index in reversed(range(150))
            ],
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "legacy-pages",
            db_path=tmp_path / "wiki.db",
            crypto_market_research_db_path=source_path,
        )
    )
    service.initialize()
    service.repository().initialize()
    legacy_index = service.config.root_path / "index.md"
    before = (
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_path.stat().st_mtime_ns,
    )
    adapters, publisher, projection_writer = (
        jue_wiki_runner._build_v3_scope_dependencies(service, "binance")
    )

    result = run_v3_scope(
        service=service,
        scope="binance",
        adapters=adapters,
        publisher=publisher,
        projection_writer=projection_writer,
    )
    replay_adapters, replay_publisher, replay_writer = (
        jue_wiki_runner._build_v3_scope_dependencies(service, "binance")
    )
    replay = run_v3_scope(
        service=service,
        scope="binance",
        adapters=replay_adapters,
        publisher=replay_publisher,
        projection_writer=replay_writer,
    )
    after = (
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_path.stat().st_mtime_ns,
    )
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            UPDATE crypto_symbol_notes
            SET summary_md = ?, updated_at = ?
            WHERE symbol = ?
            """,
            (
                "Momentum strengthened after new evidence",
                "2026-07-11T12:00:00+00:00",
                "BTC000USDT",
            ),
        )
    updated_source_state = (
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_path.stat().st_mtime_ns,
    )
    updated_adapters, updated_publisher, updated_writer = (
        jue_wiki_runner._build_v3_scope_dependencies(service, "binance")
    )
    updated = run_v3_scope(
        service=service,
        scope="binance",
        adapters=updated_adapters,
        publisher=updated_publisher,
        projection_writer=updated_writer,
    )
    after_updated_run = (
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_path.stat().st_mtime_ns,
    )

    assert result["status"] == "ok"
    assert result["candidate_count"] == 100
    assert replay["status"] == "ok"
    assert replay["snapshot_id"] == result["snapshot_id"]
    assert replay_adapters[0].symbols == adapters[0].symbols
    assert before == after
    assert updated["status"] == "ok"
    assert updated["snapshot_id"] != replay["snapshot_id"]
    assert updated_source_state == after_updated_run
    assert projection_writer.projection_root == (
        service.config.root_path / ".v3" / "binance"
    )
    assert projection_writer.projection_root.is_symlink()
    assert legacy_index.is_file()
    assert not legacy_index.is_symlink()
    assert legacy_index.read_text(encoding="utf-8").startswith("# Jue Wiki Index")


def test_default_kis_source_is_bounded_and_read_only(tmp_path: Path) -> None:
    source_path = tmp_path / "naver-source.db"
    with sqlite3.connect(source_path) as conn:
        conn.executescript(
            """
            CREATE TABLE reports (
                report_id INTEGER PRIMARY KEY,
                doc_id TEXT,
                category TEXT,
                title TEXT,
                company_name TEXT,
                broker TEXT,
                analyst TEXT,
                symbol TEXT,
                published_at TEXT,
                crawled_at TEXT,
                pdf_sha256 TEXT,
                pdf_url TEXT,
                pdf_archived_path TEXT,
                content_source TEXT,
                detail_url TEXT,
                updated_at TEXT
            );
            CREATE TABLE report_symbol_links (
                report_id INTEGER,
                symbol TEXT,
                name TEXT,
                asset_class TEXT,
                link_type TEXT,
                confidence REAL,
                evidence TEXT
            );
            CREATE TABLE report_facts (
                report_id INTEGER PRIMARY KEY,
                rating TEXT,
                target_price_value INTEGER,
                target_price_currency TEXT,
                target_price_changed TEXT,
                valuation_method TEXT,
                valuation_value REAL,
                valuation_basis TEXT,
                valuation_notes TEXT,
                summary_bullets_json TEXT,
                investment_thesis_json TEXT,
                risks_json TEXT,
                earnings_outlook_json TEXT,
                catalysts_json TEXT,
                evidence_quotes_json TEXT,
                updated_at TEXT
            );
            """
        )
        source_rows = [
            (index + 1, f"{100_000 + index:06d}")
            for index in reversed(range(150))
        ]
        conn.executemany(
            "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    report_id,
                    f"report-{report_id}",
                    "company_analysis",
                    f"Company {symbol} outlook",
                    f"Company {symbol}",
                    "Example",
                    "Analyst",
                    symbol,
                    "2026-07-10T00:00:00+00:00",
                    "2026-07-10T01:00:00+00:00",
                    f"{report_id:064x}",
                    f"https://example.invalid/{report_id}.pdf",
                    f"/evidence/report-{report_id}.pdf",
                    "pdf",
                    f"https://example.invalid/{report_id}",
                    "2026-07-10T01:00:00+00:00",
                )
                for report_id, symbol in source_rows
            ],
        )
        conn.executemany(
            "INSERT INTO report_symbol_links VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    report_id,
                    symbol,
                    f"Company {symbol}",
                    "stock",
                    "primary",
                    0.99,
                    "title",
                )
                for report_id, symbol in source_rows
            ],
        )
        conn.executemany(
            "INSERT INTO report_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    report_id,
                    "BUY",
                    100_000,
                    "KRW",
                    "UP",
                    "PER",
                    12.0,
                    "forward",
                    "",
                    '["forecast revised"]',
                    '["revision up"]',
                    '["demand slowdown"]',
                    "[]",
                    '["new product"]',
                    '["forecast revised"]',
                    "2026-07-10T01:00:00+00:00",
                )
                for report_id, _symbol in source_rows
            ],
        )
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "pages",
            db_path=tmp_path / "wiki.db",
            naver_reports_db_path=source_path,
        )
    )
    service.initialize()
    before = (
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_path.stat().st_mtime_ns,
    )
    adapters, publisher, projection_writer = (
        jue_wiki_runner._build_v3_scope_dependencies(service, "kis")
    )

    result = run_v3_scope(
        service=service,
        scope="kis",
        adapters=adapters,
        publisher=publisher,
        projection_writer=projection_writer,
    )
    replay_adapters, replay_publisher, replay_writer = (
        jue_wiki_runner._build_v3_scope_dependencies(service, "kis")
    )
    replay = run_v3_scope(
        service=service,
        scope="kis",
        adapters=replay_adapters,
        publisher=replay_publisher,
        projection_writer=replay_writer,
    )
    after = (
        hashlib.sha256(source_path.read_bytes()).hexdigest(),
        source_path.stat().st_mtime_ns,
    )

    assert result["status"] == "ok"
    assert result["candidate_count"] == 100
    assert replay["status"] == "ok"
    assert replay["snapshot_id"] == result["snapshot_id"]
    assert replay_adapters[0].symbols == adapters[0].symbols
    assert before == after
    assert adapters[0].symbols == tuple(f"{100_000 + index:06d}" for index in range(100))
    assert projection_writer.projection_root == service.config.root_path / ".v3" / "kis"


def test_bounded_source_symbols_are_canonical_before_limit(tmp_path: Path) -> None:
    source_path = tmp_path / "many-symbols.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute("CREATE TABLE crypto_symbol_notes (symbol TEXT)")
        conn.executemany(
            "INSERT INTO crypto_symbol_notes VALUES (?)",
            [(f"SYM{index:03d}",) for index in reversed(range(150))],
        )

    symbols = jue_wiki_runner._read_only_source_symbols(
        source_path,
        scope="binance",
    )

    assert symbols == tuple(f"SYM{index:03d}" for index in range(100))


def test_default_projection_rejects_symlinked_v3_parent(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "pages",
            db_path=tmp_path / "wiki.db",
        )
    )
    service.config.root_path.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (service.config.root_path / ".v3").symlink_to(outside, target_is_directory=True)

    try:
        jue_wiki_runner._build_v3_scope_dependencies(service, "kis")
    except ValueError as exc:
        assert str(exc) == "v3_projection_parent_must_be_owned_directory"
    else:
        raise AssertionError("symlinked V3 projection parent must be rejected")
    assert list(outside.iterdir()) == []


def test_default_projection_rejects_use_time_parent_symlink_substitution(
    tmp_path: Path,
) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "pages",
            db_path=tmp_path / "wiki.db",
        )
    )
    service.initialize()
    projection_parent = service.config.root_path / ".v3"
    projection_parent.mkdir()
    _adapters, _publisher, projection_writer = (
        jue_wiki_runner._build_v3_scope_dependencies(service, "kis")
    )
    parked_parent = service.config.root_path / ".v3-parked"
    projection_parent.rename(parked_parent)
    outside = tmp_path / "outside-use-time"
    outside.mkdir()
    projection_parent.symlink_to(outside, target_is_directory=True)

    try:
        projection_writer.project(_empty_snapshot("snapshot:kis:containment"))
    except WikiProjectionError as exc:
        assert str(exc) == "projection_containment_invalid"
    else:
        raise AssertionError("use-time V3 parent substitution must be rejected")
    assert list(outside.iterdir()) == []


def test_run_once_attributes_preexisting_v3_symlink_to_setup_only(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "pages"
    root_path.mkdir()
    outside = tmp_path / "outside-setup"
    outside.mkdir()
    service = JueWikiService(
        JueWikiConfig(
            root_path=root_path,
            db_path=tmp_path / "wiki.db",
        )
    )
    service.project_status_snapshot(
        v3_run_results={
            "kis": {
                "v3_compile": {"status": "error"},
                "v3_publish": {"status": "warning"},
                "v3_projection": {"status": "warning"},
                "cleanup_warnings": ["prior_setup_warning"],
            }
        }
    )
    (root_path / ".v3").symlink_to(outside, target_is_directory=True)

    result = jue_wiki_runner.run_once(
        service=service,
        state_path=tmp_path / "state.json",
        investment_memory_db_path=tmp_path / "investment-memory.db",
        performance_db_path=tmp_path / "performance.db",
        market_judgment_db_path=tmp_path / "market-judgment.db",
        repair_enabled=False,
        application_enabled=False,
    )

    for scope in ("kis", "binance"):
        scope_result = result["v3"][scope]
        assert scope_result["status"] == "error"
        assert scope_result["v3_ingest"]["status"] == "error"
        assert scope_result["v3_ingest"]["phase"] == "setup"
        assert scope_result["v3_ingest"]["error_message"] == (
            "v3_projection_parent_must_be_owned_directory"
        )
        for stage in ("v3_compile", "v3_lint", "v3_publish", "v3_projection"):
            assert scope_result[stage]["status"] == "skipped"
            assert scope_result[stage]["error_message"] == "v3_setup_failed"
    assert result["rebuild"]["status"] == "ok"
    assert result["lint"]["status"] == "ok"
    assert result["ops_snapshot"]["v3"]["last_compile_status"] == "error"
    assert result["ops_snapshot"]["v3"]["last_publish_status"] == "warning"
    assert result["ops_snapshot"]["v3"]["last_projection_status"] == "warning"
    assert result["ops_snapshot"]["v3"]["cleanup_warnings_by_scope"] == {
        "kis": ["prior_setup_warning"],
        "binance": [],
    }
    assert list(outside.iterdir()) == []


def test_repository_initialize_failure_is_setup_only_and_other_scope_continues(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    real_repository = service.repository()
    repository_calls = 0
    projected: list[str] = []

    class FailingRepository:
        def initialize(self) -> None:
            raise RuntimeError("repository_initialize_failed")

    class ForbiddenProjection:
        def project(self, snapshot: WikiSnapshotV1) -> None:
            projected.append(snapshot.snapshot_id)
            raise AssertionError("setup failure must not project")

    def repository() -> object:
        nonlocal repository_calls
        repository_calls += 1
        return FailingRepository() if repository_calls == 1 else real_repository

    def dependencies(
        _service: JueWikiService,
        scope: str,
    ) -> tuple[tuple[object, ...], JueWikiPublisherV1, object | None]:
        return (
            (),
            JueWikiPublisherV1(real_repository),
            ForbiddenProjection() if scope == "kis" else None,
        )

    monkeypatch.setattr(service, "repository", repository)
    monkeypatch.setattr(
        jue_wiki_runner,
        "_build_v3_scope_dependencies",
        dependencies,
    )

    result = jue_wiki_runner.run_once(
        service=service,
        state_path=tmp_path / "state.json",
        investment_memory_db_path=tmp_path / "investment-memory.db",
        performance_db_path=tmp_path / "performance.db",
        market_judgment_db_path=tmp_path / "market-judgment.db",
        repair_enabled=False,
        application_enabled=False,
    )

    failed = result["v3"]["kis"]
    assert failed["status"] == "error"
    assert failed["v3_ingest"]["status"] == "error"
    assert failed["v3_ingest"]["phase"] == "setup"
    assert failed["v3_ingest"]["error_message"] == "repository_initialize_failed"
    for stage in ("v3_compile", "v3_lint", "v3_publish", "v3_projection"):
        assert failed[stage]["status"] == "skipped"
        assert failed[stage]["error_message"] == "v3_setup_failed"
    assert result["v3"]["binance"]["status"] == "ok"
    assert result["rebuild"]["status"] == "ok"
    assert result["lint"]["status"] == "ok"
    assert projected == []


def test_repeated_runner_cycle_reuses_unchanged_v3_snapshot(tmp_path: Path) -> None:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "pages",
            db_path=tmp_path / "wiki.db",
        )
    )

    first = jue_wiki_runner.run_once(
        service=service,
        state_path=tmp_path / "first-state.json",
        investment_memory_db_path=tmp_path / "investment-memory.db",
        performance_db_path=tmp_path / "performance.db",
        market_judgment_db_path=tmp_path / "market-judgment.db",
        repair_enabled=False,
        application_enabled=False,
    )
    second = jue_wiki_runner.run_once(
        service=service,
        state_path=tmp_path / "second-state.json",
        investment_memory_db_path=tmp_path / "investment-memory.db",
        performance_db_path=tmp_path / "performance.db",
        market_judgment_db_path=tmp_path / "market-judgment.db",
        repair_enabled=False,
        application_enabled=False,
    )

    for scope in ("kis", "binance"):
        assert first["v3"][scope]["status"] == "ok"
        assert second["v3"][scope]["status"] == "ok"
        assert second["v3"][scope]["snapshot_id"] == (
            first["v3"][scope]["snapshot_id"]
        )
