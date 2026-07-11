from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

from tradecraft.services.jue_wiki_contract import (
    CandidateArtifactV1,
    EvidenceRefV1,
    WikiClaimV3,
)
from tradecraft.services.jue_wiki_compiler import JueWikiPublisherV1
from tradecraft.services.jue_wiki_lint import lint_snapshot
from tradecraft.services.jue_wiki_sources import (
    CryptoWikiSourceAdapter,
    JueWikiBackfillError,
    JueWikiBackfillService,
    JueWikiSourceDataError,
    JueWikiSourceSchemaError,
    NaverWikiSourceAdapter,
    SQLiteWikiBackfillCheckpointStore,
)
from tradecraft.services.jue_wiki_repository import JueWikiRepository


class _NaverResearchRepository:
    def __init__(self, *, include_hash: bool = True) -> None:
        self.include_hash = include_hash
        self.read_count = 0

    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.read_count += 1
        report = {
            "report_id": 42,
            "symbol": symbol,
            "published_at": "2026-07-10T00:00:00+00:00",
            "broker": "example",
            "link_confidence": 0.99,
            "pdf_archived_path": "/evidence/report-42.pdf",
        }
        if self.include_hash:
            report["pdf_sha256"] = "f" * 64
        return [report][:limit]

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        self.read_count += 1
        if report_id != 42:
            return None
        return {
            "rating": "BUY",
            "target_price": {"value": 100_000},
            "catalysts": ["revision up"],
            "risks": ["demand slowdown"],
            "evidence_quotes": ["forecast revised"],
        }


class _DuplicateNaverResearchRepository(_NaverResearchRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fact_reads = 0

    def latest_symbol_linked_reports(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        report = super().latest_symbol_linked_reports(symbol, limit=limit)[0]
        return [
            {**report, "link_type": "primary"},
            {**report, "link_type": "alias"},
        ]

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        self.fact_reads += 1
        return super().get_report_facts(report_id)


def test_naver_adapter_preserves_report_hash_and_source_identity() -> None:
    repo = _NaverResearchRepository()

    artifacts = NaverWikiSourceAdapter(repo).collect(
        symbols=("005930",),
        observed_at="2026-07-11T00:00:00+00:00",
    )

    evidence = artifacts[0].source_refs[0]
    assert evidence.evidence_id == "naver-report:42"
    assert evidence.content_hash == "f" * 64
    assert evidence.hash_origin == "source"
    assert artifacts[0].claims[0].evidence == (evidence,)
    assert artifacts[0].claims[0].status == "verified"


def test_source_adapter_uses_only_repository_read_contract() -> None:
    repo = _NaverResearchRepository()

    NaverWikiSourceAdapter(repo).collect(
        symbols=("005930",),
        observed_at="2026-07-11T00:00:00+00:00",
    )

    assert repo.read_count == 2


def test_naver_adapter_hashes_normalized_immutable_payload_when_hash_missing() -> None:
    artifacts = NaverWikiSourceAdapter(
        _NaverResearchRepository(include_hash=False)
    ).collect(
        symbols=("005930",),
        observed_at="2026-07-11T00:00:00+00:00",
    )

    evidence = artifacts[0].source_refs[0]
    assert len(evidence.content_hash) == 64
    assert evidence.hash_origin == "normalized_payload"
    assert artifacts[0].claims[0].status == "verified"


def test_naver_adapter_identity_includes_extractor_model_prompt_and_config() -> None:
    kwargs = {
        "symbols": ("005930",),
        "observed_at": "2026-07-11T00:00:00+00:00",
    }
    first = NaverWikiSourceAdapter(
        _NaverResearchRepository(),
        extractor_version="naver-v7",
        model="gpt-example",
        prompt_hash="p" * 64,
        config_hash="c" * 64,
    ).collect(**kwargs)[0]
    replay = NaverWikiSourceAdapter(
        _NaverResearchRepository(),
        extractor_version="naver-v7",
        model="gpt-example",
        prompt_hash="p" * 64,
        config_hash="c" * 64,
    ).collect(**kwargs)[0]
    changed = NaverWikiSourceAdapter(
        _NaverResearchRepository(),
        extractor_version="naver-v8",
        model="gpt-example",
        prompt_hash="p" * 64,
        config_hash="c" * 64,
    ).collect(**kwargs)[0]

    assert replay == first
    assert changed.artifact_id != first.artifact_id


def test_naver_same_report_for_two_symbols_has_distinct_artifact_identity() -> None:
    artifacts = NaverWikiSourceAdapter(_NaverResearchRepository()).collect(
        symbols=("005930", "000660"),
        observed_at="2026-07-11T00:00:00+00:00",
    )

    assert len(artifacts) == 2
    assert len({row.artifact_id for row in artifacts}) == 2


def test_naver_deduplicates_link_rows_before_reading_facts() -> None:
    repository = _DuplicateNaverResearchRepository()

    artifacts = NaverWikiSourceAdapter(repository).collect(
        symbols=("005930",),
        observed_at="2026-07-11T00:00:00+00:00",
    )

    assert repository.fact_reads == 1
    assert len(artifacts[0].source_refs) == 1


def test_naver_duplicate_prefers_highest_finite_link_confidence() -> None:
    repository = _DuplicateNaverResearchRepository()
    base = repository.latest_symbol_linked_reports

    def reports(symbol: str, *, limit: int) -> list[dict[str, Any]]:
        row = base(symbol, limit=limit)[0]
        return [
            {
                **row,
                "link_confidence": 0.2,
                "published_at": "2026-07-11T00:00:00Z",
                "pdf_archived_path": "/low.pdf",
            },
            {
                **row,
                "link_confidence": 0.95,
                "published_at": "2026-07-10T00:00:00Z",
                "pdf_archived_path": "/high.pdf",
            },
            {**row, "link_confidence": math.inf, "pdf_archived_path": "/inf.pdf"},
        ]

    repository.latest_symbol_linked_reports = reports  # type: ignore[method-assign]
    artifact = NaverWikiSourceAdapter(repository).collect(
        symbols=("005930",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert repository.fact_reads == 1
    assert artifact.source_refs[0].source_path == "/high.pdf"


def test_naver_duplicate_conflicting_source_hashes_fail_closed() -> None:
    repository = _DuplicateNaverResearchRepository()
    base = repository.latest_symbol_linked_reports

    def reports(symbol: str, *, limit: int) -> list[dict[str, Any]]:
        row = base(symbol, limit=limit)[0]
        return [
            {**row, "pdf_sha256": "a" * 64},
            {**row, "pdf_sha256": "b" * 64},
        ]

    repository.latest_symbol_linked_reports = reports  # type: ignore[method-assign]

    with pytest.raises(
        JueWikiSourceDataError,
        match="naver_source_hash_conflict:42:005930",
    ):
        NaverWikiSourceAdapter(repository).collect(
            symbols=("005930",), observed_at="2026-07-11T00:00:00Z"
        )

    assert repository.fact_reads == 0


def test_naver_duplicate_invalid_high_confidence_preserves_agreed_valid_hash() -> None:
    repository = _DuplicateNaverResearchRepository()
    base = repository.latest_symbol_linked_reports

    def reports(symbol: str, *, limit: int) -> list[dict[str, Any]]:
        row = base(symbol, limit=limit)[0]
        return [
            {
                **row,
                "link_confidence": 0.99,
                "pdf_sha256": "invalid",
                "pdf_archived_path": "/high-invalid.pdf",
                "pdf_url": "https://invalid.example/report.pdf",
            },
            {
                **row,
                "link_confidence": 0.80,
                "pdf_sha256": "A" * 64,
                "pdf_archived_path": "/lower-valid.pdf",
                "pdf_url": "https://valid.example/report.pdf",
            },
        ]

    repository.latest_symbol_linked_reports = reports  # type: ignore[method-assign]
    artifact = NaverWikiSourceAdapter(repository).collect(
        symbols=("005930",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    evidence = artifact.source_refs[0]

    assert repository.fact_reads == 1
    assert evidence.content_hash == "a" * 64
    assert evidence.hash_origin == "source"
    assert evidence.source_path == "/lower-valid.pdf"
    assert artifact.claims[0].status == "verified"


@pytest.mark.parametrize("invalid_hash", ["not-hex", "a" * 63, "g" * 64])
def test_naver_invalid_source_hash_uses_normalized_payload(
    invalid_hash: str,
) -> None:
    repository = _NaverResearchRepository()
    original = repository.latest_symbol_linked_reports

    def reports(symbol: str, *, limit: int) -> list[dict[str, Any]]:
        rows = original(symbol, limit=limit)
        rows[0]["pdf_sha256"] = invalid_hash
        return rows

    repository.latest_symbol_linked_reports = reports  # type: ignore[method-assign]
    evidence = NaverWikiSourceAdapter(repository).collect(
        symbols=("005930",),
        observed_at="2026-07-11T00:00:00+00:00",
    )[0].source_refs[0]

    assert evidence.hash_origin == "normalized_payload"
    assert len(evidence.content_hash) == 64


def _create_crypto_source(path: Path) -> None:
    with sqlite3.connect(path) as conn:
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
        conn.execute(
            """
            INSERT INTO crypto_symbol_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "bullish",
                "swing",
                0.72,
                "Momentum is constructive",
                '["breakout"]',
                '["funding crowding"]',
                '["daily close"]',
                "2026-07-10T12:00:00+00:00",
            ),
        )


def _file_state(path: Path) -> tuple[str, int] | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def _sqlite_state(path: Path) -> dict[str, tuple[str, int] | None]:
    return {
        suffix: _file_state(Path(f"{path}{suffix}"))
        for suffix in ("", "-wal", "-shm", "-journal")
    }


def assert_configured_source_paths_unchanged(
    source_paths: Sequence[Path],
    read_action: Callable[[], Any],
) -> None:
    """Assert explicit configured source DBs and sidecars remain unchanged."""
    before = {path.resolve(): _sqlite_state(path.resolve()) for path in source_paths}
    read_action()
    after = {path.resolve(): _sqlite_state(path.resolve()) for path in source_paths}
    assert after == before


def test_crypto_adapter_reads_sqlite_in_place_without_mutating_source(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_source(source_path)
    artifacts: tuple[CandidateArtifactV1, ...] = ()

    def collect() -> None:
        nonlocal artifacts
        artifacts = CryptoWikiSourceAdapter((source_path,)).collect(
            symbols=("BTCUSDT",),
            observed_at="2026-07-11T00:00:00+00:00",
        )

    assert_configured_source_paths_unchanged((source_path,), collect)
    evidence = artifacts[0].source_refs[0]
    assert evidence.evidence_id.startswith("crypto_symbol_note:BTCUSDT:")
    assert len(evidence.evidence_id.rsplit(":", 1)[1]) == 16
    assert evidence.source_type == "crypto_symbol_note"
    assert evidence.source_id == "BTCUSDT"
    assert evidence.hash_origin == "normalized_payload"
    assert artifacts[0].claims[0].status == "draft"
    assert artifacts[0].claims[0].evidence == (evidence,)
    assert artifacts[0].scope == "binance"
    assert artifacts[0].claims[0].scope == "binance"


def test_crypto_binance_scope_recomputes_identity_from_legacy_crypto_scope(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_source(source_path)
    artifact = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    input_payload = {
        "symbol": "BTCUSDT",
        "stance": "bullish",
        "horizon": "swing",
        "confidence": 0.72,
        "summary_md": "Momentum is constructive",
        "reasons": ["breakout"],
        "risks": ["funding crowding"],
        "triggers": ["daily close"],
        "updated_at": "2026-07-10T12:00:00+00:00",
    }
    legacy_claim = replace(
        artifact.claims[0],
        claim_id="__candidate_artifact_id__:interpretation:0",
        provenance_id="__candidate_artifact_id__",
        scope="crypto",
    )
    def digest(payload: Any) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    legacy_input_hash = digest(input_payload)
    legacy_artifact_id = digest(
        {
            "scope": "crypto",
            "input_hash": legacy_input_hash,
            "source_refs": [row.to_dict() for row in artifact.source_refs],
            "claims": [legacy_claim.to_dict()],
            "relationships": [],
            "extractor_version": artifact.extractor_version,
            "model": artifact.model,
            "prompt_hash": artifact.prompt_hash,
            "config_hash": artifact.config_hash,
        }
    )

    assert artifact.scope == "binance"
    assert artifact.artifact_id != legacy_artifact_id
    assert artifact.input_hash != legacy_input_hash


def test_crypto_artifact_compiles_and_publishes_in_binance_scope(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "crypto.db"
    wiki_path = tmp_path / "wiki.db"
    _create_crypto_source(source_path)
    artifact = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    repository = JueWikiRepository(wiki_path)
    repository.initialize()
    for evidence in artifact.source_refs:
        repository.register_evidence(evidence)
    repository.store_candidate(artifact)

    snapshot = JueWikiPublisherV1(repository).compile_and_publish(
        scope="binance",
        artifact_ids=(artifact.artifact_id,),
    )
    findings = lint_snapshot(
        snapshot,
        known_evidence_ids=repository.evidence_ids(),
    )

    assert repository.current_snapshot("binance") == snapshot
    assert all(page.scope == "binance" for page in snapshot.pages)
    assert not [row for row in findings if row.finding_type == "cross_scope_claim"]


def test_crypto_adapter_exact_replay_is_deterministic(tmp_path: Path) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_source(source_path)
    adapter = CryptoWikiSourceAdapter((source_path,))
    kwargs = {
        "symbols": ("BTCUSDT",),
        "observed_at": "2026-07-11T00:00:00+00:00",
    }

    first = adapter.collect(**kwargs)
    replay = adapter.collect(**kwargs)

    assert replay == first
    assert replay[0].source_refs[0].evidence_id == first[0].source_refs[0].evidence_id
    assert replay[0].source_refs[0].source_id == "BTCUSDT"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("summary_md", "Changed content"),
        ("content_hash", "b" * 64),
        ("updated_at", "2026-07-10T13:00:00Z"),
    ],
)
def test_crypto_mutable_row_changes_create_new_evidence_version(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_source(source_path)
    with sqlite3.connect(source_path) as conn:
        conn.execute("ALTER TABLE crypto_symbol_notes ADD COLUMN content_hash TEXT")
        conn.execute(
            "UPDATE crypto_symbol_notes SET content_hash = ?",
            ("a" * 64,),
        )
    adapter = CryptoWikiSourceAdapter(source_path)
    first = adapter.collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            f"UPDATE crypto_symbol_notes SET {column} = ? WHERE symbol = ?",
            (value, "BTCUSDT"),
        )
    updated = adapter.collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert updated.source_refs[0].source_id == first.source_refs[0].source_id
    assert updated.source_refs[0].evidence_id != first.source_refs[0].evidence_id
    assert updated.artifact_id != first.artifact_id


def test_crypto_evidence_versions_coexist_and_publish_over_compiler_base(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "crypto.db"
    wiki_path = tmp_path / "wiki.db"
    _create_crypto_source(source_path)
    adapter = CryptoWikiSourceAdapter(source_path)
    first = adapter.collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            UPDATE crypto_symbol_notes
            SET summary_md = ?, updated_at = ?
            WHERE symbol = ?
            """,
            ("Updated momentum", "2026-07-10T13:00:00Z", "BTCUSDT"),
        )
    updated = adapter.collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    repository = JueWikiRepository(wiki_path)
    repository.initialize()
    for artifact in (first, updated):
        for evidence in artifact.source_refs:
            repository.register_evidence(evidence)
        repository.store_candidate(artifact)
    publisher = JueWikiPublisherV1(repository)
    base = publisher.compile_and_publish(
        scope="binance", artifact_ids=(first.artifact_id,)
    )
    snapshot = publisher.compile_and_publish(
        scope="binance", artifact_ids=(updated.artifact_id,)
    )
    findings = lint_snapshot(
        snapshot,
        known_evidence_ids=repository.evidence_ids(),
    )
    compiled_evidence_ids = {
        evidence.evidence_id
        for page in snapshot.pages
        for claim in page.claims
        for evidence in claim.evidence
    }

    assert base.snapshot_id != snapshot.snapshot_id
    assert compiled_evidence_ids == {
        first.source_refs[0].evidence_id,
        updated.source_refs[0].evidence_id,
    }
    assert not [
        row
        for row in findings
        if row.severity == "error"
        or row.finding_type == "evidence_payload_conflict"
    ]


def test_crypto_adapter_prefers_existing_source_hash(tmp_path: Path) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_source(source_path)
    with sqlite3.connect(source_path) as conn:
        conn.execute("ALTER TABLE crypto_symbol_notes ADD COLUMN content_hash TEXT")
        conn.execute(
            "UPDATE crypto_symbol_notes SET content_hash = ?",
            ("e" * 64,),
        )

    artifacts = CryptoWikiSourceAdapter((source_path,)).collect(
        symbols=("BTCUSDT",),
        observed_at="2026-07-11T00:00:00+00:00",
    )

    assert artifacts[0].source_refs[0].content_hash == "e" * 64
    assert artifacts[0].source_refs[0].hash_origin == "source"


def test_crypto_invalid_source_hash_falls_back_and_lowercases_valid_hash(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_source(source_path)
    with sqlite3.connect(source_path) as conn:
        conn.execute("ALTER TABLE crypto_symbol_notes ADD COLUMN content_hash TEXT")
        conn.execute(
            "UPDATE crypto_symbol_notes SET content_hash = ?",
            ("A" * 64,),
        )
    valid = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    with sqlite3.connect(source_path) as conn:
        conn.execute("UPDATE crypto_symbol_notes SET content_hash = 'invalid'")
    invalid = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert valid.source_refs[0].content_hash == "a" * 64
    assert valid.source_refs[0].hash_origin == "source"
    assert invalid.source_refs[0].hash_origin == "normalized_payload"


def test_crypto_same_source_in_different_db_paths_has_distinct_artifact_id(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    _create_crypto_source(first_path)
    _create_crypto_source(second_path)

    first = CryptoWikiSourceAdapter(first_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    second = CryptoWikiSourceAdapter(second_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert first.artifact_id != second.artifact_id


def test_crypto_identical_content_across_databases_dedupes_consistently(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    _create_crypto_source(first_path)
    _create_crypto_source(second_path)

    forward = CryptoWikiSourceAdapter((first_path, second_path)).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )
    reverse = CryptoWikiSourceAdapter((second_path, first_path)).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(forward) == 1
    assert reverse == forward


def test_crypto_different_content_across_databases_versions_logical_source(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    _create_crypto_source(first_path)
    _create_crypto_source(second_path)
    with sqlite3.connect(second_path) as conn:
        conn.execute(
            "UPDATE crypto_symbol_notes SET summary_md = ?",
            ("Different content",),
        )

    artifacts = CryptoWikiSourceAdapter((first_path, second_path)).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 2
    assert {row.source_refs[0].source_id for row in artifacts} == {"BTCUSDT"}
    assert len({row.source_refs[0].evidence_id for row in artifacts}) == 2


def test_crypto_identical_duplicate_rows_in_one_source_are_deduplicated(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "duplicates.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_symbol_notes (
                symbol TEXT, summary_md TEXT, updated_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO crypto_symbol_notes VALUES (?, ?, ?)",
            [("BTCUSDT", "same", "2026-07-10T00:00:00Z")] * 2,
        )

    artifacts = CryptoWikiSourceAdapter(source_path).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 1


def test_crypto_same_logical_identity_with_different_payload_is_versioned(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "collision.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_features (
                symbol TEXT, feature_json TEXT, updated_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO crypto_features VALUES (?, ?, ?)",
            [
                ("BTCUSDT", '{"score": 1}', "2026-07-10T00:00:00Z"),
                ("BTCUSDT", '{"score": 2}', "2026-07-10T00:00:00Z"),
            ],
        )

    artifacts = CryptoWikiSourceAdapter(source_path).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 2
    assert len({row.artifact_id for row in artifacts}) == 2
    assert len({row.source_refs[0].evidence_id for row in artifacts}) == 2
    assert {row.source_refs[0].source_id for row in artifacts} == {"BTCUSDT"}


def _create_crypto_table_rows(path: Path, table: str, count: int) -> None:
    with sqlite3.connect(path) as conn:
        if table == "crypto_symbol_notes":
            conn.execute(
                """
                CREATE TABLE crypto_symbol_notes (
                    symbol TEXT PRIMARY KEY, summary_md TEXT, reasons_json TEXT,
                    risks_json TEXT, triggers_json TEXT, updated_at TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO crypto_symbol_notes VALUES (?, ?, '[]', '[]', '[]', ?)",
                [
                    (f"N{index:03d}USDT", f"note {index}", "2026-07-10T00:00:00Z")
                    for index in range(count)
                ],
            )
        elif table == "crypto_candidates":
            conn.execute(
                """
                CREATE TABLE crypto_candidates (
                    symbol TEXT, market TEXT, stance TEXT, horizon TEXT,
                    reason_md TEXT, block_template_json TEXT, updated_at TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO crypto_candidates VALUES (?, 'spot', 'long', 'swing', ?, '{}', ?)",
                [
                    (f"C{index:03d}USDT", f"candidate {index}", "2026-07-10T00:00:00Z")
                    for index in range(count)
                ],
            )
        else:
            conn.execute(
                """
                CREATE TABLE crypto_features (
                    symbol TEXT PRIMARY KEY, feature_json TEXT, updated_at TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO crypto_features VALUES (?, '{}', ?)",
                [
                    (f"F{index:03d}USDT", "2026-07-10T00:00:00Z")
                    for index in range(count)
                ],
            )


@pytest.mark.parametrize(
    "table",
    ["crypto_symbol_notes", "crypto_candidates", "crypto_features"],
)
def test_crypto_each_table_is_bounded_with_empty_symbol_filter(
    tmp_path: Path,
    table: str,
) -> None:
    source_path = tmp_path / f"{table}.db"
    _create_crypto_table_rows(source_path, table, 130)

    artifacts = CryptoWikiSourceAdapter(source_path, max_rows=100).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 100


def test_crypto_max_rows_is_global_across_tables(tmp_path: Path) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_table_rows(source_path, "crypto_symbol_notes", 60)
    _create_crypto_table_rows(source_path, "crypto_candidates", 60)
    _create_crypto_table_rows(source_path, "crypto_features", 60)

    artifacts = CryptoWikiSourceAdapter(source_path, max_rows=100).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 100


def test_crypto_budget_counts_every_fetched_duplicate_and_invalid_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    with sqlite3.connect(first_path) as conn:
        conn.execute(
            "CREATE TABLE crypto_features (symbol TEXT, feature_json TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO crypto_features VALUES (?, '{}', ?)",
            [
                ("BTCUSDT", "2026-07-10T00:00:00Z"),
                ("BTCUSDT", "2026-07-10T00:00:00Z"),
                ("ETHUSDT", "invalid-time"),
            ],
        )
    _create_crypto_table_rows(second_path, "crypto_features", 5)
    original = CryptoWikiSourceAdapter._read_table.__func__
    fetched_counts: list[int] = []

    def instrumented_read_table(cls: Any, *args: Any, **kwargs: Any) -> Any:
        rows = original(cls, *args, **kwargs)
        fetched_counts.append(len(rows))
        return rows

    monkeypatch.setattr(
        CryptoWikiSourceAdapter,
        "_read_table",
        classmethod(instrumented_read_table),
    )

    artifacts = CryptoWikiSourceAdapter(
        (first_path, second_path),
        max_rows=3,
    ).collect(symbols=(), observed_at="2026-07-11T00:00:00Z")

    assert fetched_counts == [3]
    assert sum(fetched_counts) == 3
    assert len(artifacts) == 1


@pytest.mark.parametrize(("requested", "expected"), [(0, 1), (101, 100)])
def test_crypto_max_rows_clamps_to_safe_range(
    tmp_path: Path,
    requested: int,
    expected: int,
) -> None:
    source_path = tmp_path / "crypto.db"
    _create_crypto_table_rows(source_path, "crypto_features", 130)

    artifacts = CryptoWikiSourceAdapter(source_path, max_rows=requested).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == expected


def test_crypto_missing_optional_columns_use_deterministic_defaults(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "minimal.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute("CREATE TABLE crypto_symbol_notes (symbol TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO crypto_symbol_notes VALUES ('BTCUSDT')")

    artifact = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert artifact.claims[0].symbols == ("BTCUSDT",)
    assert artifact.created_at == "2026-07-11T00:00:00+00:00"


def test_crypto_missing_identity_column_raises_clear_schema_error(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "broken.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute("CREATE TABLE crypto_features (feature_json TEXT)")

    with pytest.raises(
        JueWikiSourceSchemaError,
        match="crypto_source_identity_columns_missing:crypto_features:symbol",
    ):
        CryptoWikiSourceAdapter(source_path).collect(
            symbols=(), observed_at="2026-07-11T00:00:00Z"
        )


def test_crypto_malformed_json_is_empty_and_does_not_drop_valid_rows(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "malformed.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_symbol_notes (
                symbol TEXT PRIMARY KEY, summary_md TEXT, reasons_json TEXT,
                risks_json TEXT, triggers_json TEXT, confidence TEXT,
                updated_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO crypto_symbol_notes VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "BTCUSDT",
                    "valid",
                    "not-json",
                    None,
                    "{bad",
                    "not-a-number",
                    "2026-07-10T00:00:00Z",
                ),
                (
                    "ETHUSDT",
                    "also valid",
                    "[]",
                    "[]",
                    "[]",
                    "0.4",
                    "2026-07-10T00:00:00Z",
                ),
                (
                    None,
                    "missing identity",
                    "[]",
                    "[]",
                    "[]",
                    "0.5",
                    "2026-07-10T00:00:00Z",
                ),
            ],
        )

    artifacts = CryptoWikiSourceAdapter(source_path).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 2


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_crypto_non_finite_numbers_normalize_to_zero(
    tmp_path: Path,
    non_finite: float,
) -> None:
    source_path = tmp_path / "non-finite.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            """
            CREATE TABLE crypto_symbol_notes (
                symbol TEXT, confidence REAL, updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO crypto_symbol_notes VALUES ('BTCUSDT', ?, ?)",
            (non_finite, "2026-07-10T00:00:00Z"),
        )

    artifact = CryptoWikiSourceAdapter(source_path).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert artifact.claims[0].confidence == 0.0
    assert "NaN" not in artifact.to_dict().__repr__()
    assert "Infinity" not in artifact.to_dict().__repr__()


def test_crypto_nested_non_finite_json_numbers_normalize_to_zero(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "nested-non-finite.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            "CREATE TABLE crypto_features (symbol TEXT, feature_json TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO crypto_features VALUES ('BTCUSDT', ?, ?)",
            ('{"nan": NaN, "pos": Infinity, "neg": -Infinity}', "2026-07-10T00:00:00Z"),
        )

    artifact = CryptoWikiSourceAdapter(source_path).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert artifact.input_hash
    assert artifact.source_refs[0].hash_origin == "normalized_payload"


def test_crypto_invalid_timestamp_row_is_skipped_without_corrupting_valid_rows(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "timestamps.db"
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            "CREATE TABLE crypto_features (symbol TEXT, feature_json TEXT, updated_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO crypto_features VALUES (?, '{}', ?)",
            [("BTCUSDT", "bad-time"), ("ETHUSDT", "2026-07-10T00:00:00Z")],
        )

    artifacts = CryptoWikiSourceAdapter(source_path).collect(
        symbols=(), observed_at="2026-07-11T00:00:00Z"
    )

    assert len(artifacts) == 1
    assert artifacts[0].claims[0].symbols == ("ETHUSDT",)


def test_crypto_equivalent_timestamp_offsets_have_identical_identity(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "offset.db"
    _create_crypto_source(source_path)
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            "UPDATE crypto_symbol_notes SET updated_at = '2026-07-10T21:00:00+09:00'"
        )
    offset = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            "UPDATE crypto_symbol_notes SET updated_at = '2026-07-10T12:00:00Z'"
        )
    utc = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert offset == utc


def test_crypto_semantically_equal_json_has_identical_fallback_hash(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "json.db"
    _create_crypto_source(source_path)
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            "UPDATE crypto_symbol_notes SET reasons_json = '[ {\"b\": 2, \"a\": 1} ]'"
        )
    first = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]
    with sqlite3.connect(source_path) as conn:
        conn.execute(
            "UPDATE crypto_symbol_notes SET reasons_json = '[{\"a\":1,\"b\":2}]'"
        )
    replay = CryptoWikiSourceAdapter(source_path).collect(
        symbols=("BTCUSDT",), observed_at="2026-07-11T00:00:00Z"
    )[0]

    assert replay == first


def test_crypto_wal_main_and_wal_unchanged_while_shm_coordination_may_change(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "wal-source.db"
    writer = sqlite3.connect(source_path)
    try:
        mode = str(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            """
            CREATE TABLE crypto_features (
                symbol TEXT, feature_json TEXT, updated_at TEXT
            )
            """
        )
        writer.execute(
            "INSERT INTO crypto_features VALUES ('BTCUSDT', '{}', ?)",
            ("2026-07-10T00:00:00Z",),
        )
        writer.commit()
        wal_path = Path(f"{source_path}-wal")
        shm_path = Path(f"{source_path}-shm")
        if mode != "wal" or not wal_path.exists() or not shm_path.exists():
            pytest.skip(
                "platform SQLite did not preserve live WAL/SHM sidecars while writer held open"
            )

        before = _sqlite_state(source_path)
        artifacts = CryptoWikiSourceAdapter(source_path).collect(
            symbols=(), observed_at="2026-07-11T00:00:00Z"
        )
        after = _sqlite_state(source_path)

        assert len(artifacts) == 1
        assert after[""] == before[""]
        assert after["-wal"] == before["-wal"]
        assert after["-journal"] == before["-journal"]
        assert after["-shm"] is not None
    finally:
        writer.close()


def _artifact(source_id: str) -> CandidateArtifactV1:
    evidence = EvidenceRefV1(
        evidence_id=f"test:{source_id}",
        source_type="test",
        source_id=source_id,
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    artifact_id = f"artifact:{source_id}"
    return CandidateArtifactV1(
        artifact_id=artifact_id,
        scope="kis",
        extractor_version="test-v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(
            WikiClaimV3(
                claim_id=f"{artifact_id}:fact:0",
                claim_type="fact",
                text=source_id,
                status="verified",
                scope="kis",
                evidence=(evidence,),
                provenance_id=artifact_id,
            ),
        ),
        created_at="2026-07-11T00:00:00+00:00",
    )


class _BackfillSource:
    def __init__(self, count: int) -> None:
        self.rows = [{"source_id": f"{index:03d}"} for index in range(count)]
        self.limits: list[int] = []

    def read_after(
        self,
        *,
        scope: str,
        cursor: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str]:
        assert scope == "kis"
        self.limits.append(limit)
        rows = [row for row in self.rows if str(row["source_id"]) > cursor]
        return rows[:limit], "opaque-provider-cursor"


class _UnboundedBackfillSource(_BackfillSource):
    def read_after(
        self,
        *,
        scope: str,
        cursor: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str]:
        self.limits.append(limit)
        return self.rows, str(len(self.rows))


class _BackfillRepository:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.artifacts: dict[str, CandidateArtifactV1] = {}
        self.write_count = 0

    def store_candidate(self, artifact: CandidateArtifactV1) -> None:
        self.write_count += 1
        if self.fail_at == self.write_count:
            raise RuntimeError("candidate write failed")
        self.artifacts.setdefault(artifact.artifact_id, artifact)


class _MemoryCheckpointStore:
    def __init__(self, cursor: str = "") -> None:
        self.cursor = cursor

    def read(self, *, scope: str) -> str:
        assert scope == "kis"
        return self.cursor

    def store(self, *, scope: str, cursor: str) -> None:
        assert scope == "kis"
        self.cursor = cursor


def _backfill_service(
    *,
    source: _BackfillSource,
    repository: _BackfillRepository,
    checkpoint_store: Any = None,
) -> JueWikiBackfillService:
    return JueWikiBackfillService(
        source=source,
        artifact_builder=lambda row: _artifact(str(row["source_id"])),
        repository=repository,
        checkpoint_store=checkpoint_store,
    )


def test_backfill_defaults_to_dry_run_without_writes_or_checkpoint() -> None:
    source = _BackfillSource(2)
    repository = _BackfillRepository()

    batch = _backfill_service(source=source, repository=repository).run(
        scope="kis",
        cursor="",
    )

    assert batch.dry_run is True
    assert batch.next_cursor == "001"
    assert repository.artifacts == {}


@pytest.mark.parametrize(("requested", "bounded"), [(0, 1), (101, 100)])
def test_backfill_clamps_batch_limit(requested: int, bounded: int) -> None:
    source = _BackfillSource(150)
    repository = _BackfillRepository()

    batch = _backfill_service(source=source, repository=repository).run(
        scope="kis",
        cursor="",
        limit=requested,
    )

    assert source.limits == [bounded]
    assert batch.source_count == bounded


def test_backfill_enforces_bound_when_source_returns_too_many_rows() -> None:
    source = _UnboundedBackfillSource(150)
    repository = _BackfillRepository()

    batch = _backfill_service(source=source, repository=repository).run(
        scope="kis",
        cursor="",
        limit=100,
    )

    assert batch.source_count == 100


def test_backfill_exact_limit_unordered_rows_fail_closed() -> None:
    source = _BackfillSource(0)
    source.rows = [{"source_id": "b"}, {"source_id": "a"}]
    repository = _BackfillRepository()

    with pytest.raises(
        JueWikiBackfillError,
        match="backfill_source_order_invalid",
    ):
        _backfill_service(source=source, repository=repository).run(
            scope="kis",
            cursor="",
            limit=2,
        )


def test_failed_artifact_write_retains_previous_checkpoint() -> None:
    source = _BackfillSource(3)
    repository = _BackfillRepository(fail_at=2)
    checkpoint_store = _MemoryCheckpointStore("")

    with pytest.raises(RuntimeError, match="candidate write failed"):
        _backfill_service(
            source=source,
            repository=repository,
            checkpoint_store=checkpoint_store,
        ).run(
            scope="kis",
            cursor="",
            dry_run=False,
        )

    assert checkpoint_store.read(scope="kis") == ""


def test_backfill_restart_uses_authoritative_stored_cursor() -> None:
    source = _BackfillSource(2)
    repository = _BackfillRepository()
    checkpoint_store = _MemoryCheckpointStore("")
    service = _backfill_service(
        source=source,
        repository=repository,
        checkpoint_store=checkpoint_store,
    )

    first = service.run(scope="kis", cursor="", dry_run=False)
    replay = service.run(scope="kis", cursor="001", dry_run=False)

    assert first.artifact_ids == ("artifact:000", "artifact:001")
    assert replay.artifact_ids == ()
    assert tuple(repository.artifacts) == first.artifact_ids
    assert checkpoint_store.read(scope="kis") == "001"


def test_non_dry_backfill_requires_explicit_checkpoint_store() -> None:
    with pytest.raises(
        JueWikiBackfillError,
        match="backfill_checkpoint_store_required",
    ):
        _backfill_service(
            source=_BackfillSource(1),
            repository=_BackfillRepository(),
        ).run(scope="kis", cursor="", dry_run=False)


@pytest.mark.parametrize(
    ("stored_cursor", "caller_cursor"),
    [("010", "005"), ("005", "010")],
)
def test_non_dry_backfill_rejects_ahead_or_behind_caller_cursor(
    stored_cursor: str,
    caller_cursor: str,
) -> None:
    source = _BackfillSource(20)
    repository = _BackfillRepository()

    with pytest.raises(JueWikiBackfillError, match="backfill_cursor_mismatch"):
        _backfill_service(
            source=source,
            repository=repository,
            checkpoint_store=_MemoryCheckpointStore(stored_cursor),
        ).run(scope="kis", cursor=caller_cursor, dry_run=False)

    assert source.limits == []
    assert repository.artifacts == {}


def test_sqlite_checkpoint_store_round_trips_across_instances(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    repository = JueWikiRepository(db_path)
    repository.initialize()

    first = SQLiteWikiBackfillCheckpointStore(
        repository,
        forbidden_source_paths=(),
    )
    assert first.read(scope="kis") == ""
    first.store(scope="kis", cursor="naver_report:42")

    second = SQLiteWikiBackfillCheckpointStore(
        repository,
        forbidden_source_paths=(),
    )
    assert second.read(scope="kis") == "naver_report:42"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT scope, cursor, updated_at FROM wiki_backfill_checkpoints_v1"
        ).fetchone()
    assert row is not None
    assert row[0:2] == ("kis", "naver_report:42")
    assert str(row[2]).endswith("+00:00")


def test_sqlite_checkpoint_store_refuses_cursor_regression(tmp_path: Path) -> None:
    repository = JueWikiRepository(tmp_path / "wiki.db")
    repository.initialize()
    checkpoint_store = SQLiteWikiBackfillCheckpointStore(
        repository,
        forbidden_source_paths=(),
    )
    checkpoint_store.store(scope="kis", cursor="010")

    with pytest.raises(
        JueWikiBackfillError,
        match="backfill_checkpoint_regression",
    ):
        checkpoint_store.store(scope="kis", cursor="005")

    assert checkpoint_store.read(scope="kis") == "010"


def test_checkpoint_store_rejects_source_overlap_before_schema_write(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.db"
    _create_crypto_source(source_path)
    repository = JueWikiRepository(source_path)
    before = _sqlite_state(source_path)

    with pytest.raises(TypeError):
        SQLiteWikiBackfillCheckpointStore(repository)  # type: ignore[call-arg]

    assert _sqlite_state(source_path) == before

    with pytest.raises(
        JueWikiBackfillError,
        match="backfill_checkpoint_source_overlap",
    ):
        SQLiteWikiBackfillCheckpointStore(
            repository,
            forbidden_source_paths=(source_path,),
        )

    assert _sqlite_state(source_path) == before
    with sqlite3.connect(source_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "wiki_backfill_checkpoints_v1" not in tables


def test_process_style_replay_recovers_partial_candidate_batch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "wiki.db"
    repository = JueWikiRepository(db_path)
    repository.initialize()
    checkpoint_store = SQLiteWikiBackfillCheckpointStore(
        repository,
        forbidden_source_paths=(),
    )

    class FailingRepository:
        def __init__(self) -> None:
            self.writes = 0

        def store_candidate(self, artifact: CandidateArtifactV1) -> None:
            self.writes += 1
            if self.writes == 2:
                raise RuntimeError("process stopped")
            repository.store_candidate(artifact)

    with pytest.raises(RuntimeError, match="process stopped"):
        JueWikiBackfillService(
            source=_BackfillSource(2),
            artifact_builder=lambda row: _artifact(str(row["source_id"])),
            repository=FailingRepository(),  # type: ignore[arg-type]
            checkpoint_store=checkpoint_store,
        ).run(scope="kis", cursor="", dry_run=False)

    assert checkpoint_store.read(scope="kis") == ""
    replay_repository = JueWikiRepository(db_path)
    batch = JueWikiBackfillService(
        source=_BackfillSource(2),
        artifact_builder=lambda row: _artifact(str(row["source_id"])),
        repository=replay_repository,
        checkpoint_store=SQLiteWikiBackfillCheckpointStore(
            replay_repository,
            forbidden_source_paths=(),
        ),
    ).run(scope="kis", cursor="", dry_run=False)

    assert batch.artifact_ids == ("artifact:000", "artifact:001")
    assert replay_repository.candidate_artifacts(batch.artifact_ids) == (
        _artifact("000"),
        _artifact("001"),
    )
    assert SQLiteWikiBackfillCheckpointStore(
        replay_repository,
        forbidden_source_paths=(),
    ).read(scope="kis") == "001"


def test_unsorted_over_return_fails_closed() -> None:
    source = _UnboundedBackfillSource(0)
    source.rows = [
        {"source_id": "d"},
        {"source_id": "b"},
        {"source_id": "c"},
        {"source_id": "a"},
    ]
    service = _backfill_service(
        source=source,
        repository=_BackfillRepository(),
    )

    with pytest.raises(
        JueWikiBackfillError,
        match="backfill_source_order_invalid",
    ):
        service.run(scope="kis", cursor="", limit=2)


def test_ordered_multi_page_backfill_has_no_skip_or_duplicate() -> None:
    source = _BackfillSource(5)
    service = _backfill_service(
        source=source,
        repository=_BackfillRepository(),
    )

    first = service.run(scope="kis", cursor="", limit=2)
    second = service.run(scope="kis", cursor=first.next_cursor, limit=2)
    third = service.run(scope="kis", cursor=second.next_cursor, limit=2)

    assert first.artifact_ids == ("artifact:000", "artifact:001")
    assert second.artifact_ids == ("artifact:002", "artifact:003")
    assert third.artifact_ids == ("artifact:004",)
    assert first.next_cursor == "001"
    assert second.next_cursor == "003"
    assert third.next_cursor == "004"


def test_backfill_identical_duplicate_identity_is_deduplicated() -> None:
    source = _UnboundedBackfillSource(0)
    source.rows = [
        {"source_id": "a", "payload": 1},
        {"source_id": "a", "payload": 1},
    ]

    batch = _backfill_service(
        source=source,
        repository=_BackfillRepository(),
    ).run(scope="kis", cursor="")

    assert batch.artifact_ids == ("artifact:a",)


def test_backfill_duplicate_identity_payload_collision_fails_closed() -> None:
    source = _UnboundedBackfillSource(0)
    source.rows = [
        {"source_id": "a", "payload": 1},
        {"source_id": "a", "payload": 2},
    ]

    with pytest.raises(
        JueWikiBackfillError,
        match="backfill_source_identity_collision:a",
    ):
        _backfill_service(
            source=source,
            repository=_BackfillRepository(),
        ).run(scope="kis", cursor="")


def test_backfill_rejects_rows_without_stable_source_identity() -> None:
    source = _UnboundedBackfillSource(0)
    source.rows = [{"payload": "unaddressable"}]

    with pytest.raises(JueWikiBackfillError, match="backfill_source_identity_required"):
        _backfill_service(
            source=source,
            repository=_BackfillRepository(),
        ).run(scope="kis", cursor="")
