# Jue Wiki-First Knowledge Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Jue Wiki the evidence-backed canonical interpretation layer for KIS and Binance decisions while preserving immutable research evidence, operational trading truth, and all existing safety controls.

**Architecture:** Add a structured V3 knowledge contract and transactional repository beside the existing compiled Markdown Wiki, then move source ingestion, compilation, lint, selection, and migration policy behind focused services. Managers continue reading live state directly; they receive a versioned Wiki decision packet and a venue-specific gate that can block only new risk expansion while preserving exits, reconciliation, and kill switches.

**Tech Stack:** Python 3.10+, dataclasses, typing `Protocol`, SQLite, FastAPI, pytest, existing static runtime runners, and existing `scripts/verify.py` verification tiers.

## Global Constraints

- Preserve existing API routes, environment-variable aliases, kill switches, and paper/live defaults.
- Do not modify live settings, delete runtime data, send orders, or activate `required` mode during implementation or tests.
- Keep `observe`, `assist`, and `primary` as prompt presentation modes; add `shadow`, `prefer`, and `required` as an independent Wiki read policy.
- The default Wiki read policy is `shadow`.
- Raw evidence and operational ledgers remain authoritative for their own facts; Wiki code must not mutate them.
- A `verified` claim requires a resolvable evidence ID and a non-empty content hash.
- Stale or conflicted claims cannot be the sole support for increasing risk.
- Wiki failure may block new risk expansion but must not block exits, reconciliation, or kill switches.
- Status and readiness paths read stored projections and perform no Wiki compilation or SQLite writes.
- No new third-party dependencies.
- Use test-first changes and keep one structural change and one independent feature at most in flight.
- Do not commit unless the user separately authorizes commits.

## File Structure

- Create `src/tradecraft/services/jue_wiki_contract.py`: immutable V3 evidence, claim, relationship, page, snapshot, context, and gate contracts.
- Create `src/tradecraft/services/jue_wiki_repository.py`: schema migration, immutable artifact storage, transactional snapshot publication, and read-only queries.
- Create `src/tradecraft/services/jue_wiki_compiler.py`: pure candidate-to-page compilation and publication orchestration.
- Create `src/tradecraft/services/jue_wiki_lint.py`: provenance, lifecycle, scope, conflict, and relationship validation.
- Create `src/tradecraft/services/jue_wiki_projection.py`: deterministic Markdown and disposable search-index projection from a published snapshot.
- Create `src/tradecraft/services/jue_wiki_sources.py`: read-only Naver and crypto source adapters that create immutable candidate artifacts.
- Create `src/tradecraft/services/jue_wiki_context.py`: Wiki-first selection, quality assessment, read-mode policy, and common decision gate.
- Create `src/tradecraft/services/jue_wiki_shadow.py`: recorded comparison storage and per-venue mode eligibility.
- Create `scripts/replay_jue_wiki.py`: isolated, non-executing shadow replay for the same recorded manager input.
- Modify `src/tradecraft/services/jue_wiki.py`: retain the compatibility facade and delegate new V3 operations to focused services.
- Modify `src/tradecraft/runtime/jue_wiki_runner.py`: run V3 ingestion, compilation, lint, publication, projection, and stored readiness snapshot steps.
- Modify `src/tradecraft/runtime/kis_block_trader_runner.py` and `src/tradecraft/runtime/binance_block_trader_runner.py`: provide read-mode-aware Wiki context.
- Modify `src/tradecraft/services/kis_block_trader.py` and `src/tradecraft/services/binance_block_trader.py`: attach the gate and suppress venue-specific new-risk actions when required.
- Modify `src/tradecraft/services/manager_run_telemetry.py`: record Wiki snapshot, mode, coverage, and shadow comparison IDs.
- Modify `src/tradecraft/config.py`, `.env.example`, and `docs/spec/12_config_env.md`: add backward-compatible read policy and promotion thresholds.
- Modify `src/tradecraft/api/ops_readiness.py` and `src/tradecraft/api/ops_payloads.py`: expose stored Wiki-first eligibility and health without writes.
- Create focused tests matching each new service; do not add new V3 cases to the already large `tests/test_jue_wiki.py` unless they exercise the compatibility facade.

---

### Task 1: Freeze the V3 knowledge and decision contracts

**Files:**
- Create: `src/tradecraft/services/jue_wiki_contract.py`
- Create: `tests/test_jue_wiki_contract.py`

**Interfaces:**
- Consumes: no new application interfaces.
- Produces: `EvidenceRefV1`, `CandidateArtifactV1`, `WikiClaimV3`, `WikiRelationshipV1`, `JueWikiPageV3`, `WikiSnapshotV1`, `WikiContextRequestV1`, `WikiContextPacketV1`, `WikiDecisionGateV1`, and `WikiContractError`.

- [ ] **Step 1: Write failing contract tests**

```python
from dataclasses import replace

import pytest

from tradecraft.services.jue_wiki_contract import (
    EvidenceRefV1,
    WikiClaimV3,
    WikiContractError,
)


def test_verified_claim_requires_hashed_evidence() -> None:
    with pytest.raises(WikiContractError, match="verified_claim_requires_evidence"):
        WikiClaimV3(
            claim_id="claim:kis:005930:thesis",
            claim_type="interpretation",
            text="Earnings revisions support the thesis.",
            status="verified",
            scope="kis",
            evidence=(),
        )


def test_verified_claim_accepts_resolvable_hashed_evidence() -> None:
    evidence = EvidenceRefV1(
        evidence_id="naver-report:42",
        source_type="naver_report",
        source_id="42",
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:thesis",
        claim_type="interpretation",
        text="Earnings revisions support the thesis.",
        status="verified",
        scope="kis",
        evidence=(evidence,),
    )
    assert claim.evidence == (evidence,)
    assert replace(claim, status="stale").status == "stale"
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run: `pytest tests/test_jue_wiki_contract.py -q`

Expected: collection fails with `ModuleNotFoundError: tradecraft.services.jue_wiki_contract`.

- [ ] **Step 3: Implement the immutable contracts and validation**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ClaimType = Literal["fact", "interpretation", "hypothesis", "policy"]
ClaimStatus = Literal[
    "draft", "verified", "stale", "conflicted", "superseded", "rejected"
]
ReadMode = Literal["shadow", "prefer", "required"]


class WikiContractError(ValueError):
    pass


class _DictContract:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceRefV1(_DictContract):
    evidence_id: str
    source_type: str
    source_id: str
    content_hash: str
    observed_at: str
    source_path: str = ""
    hash_origin: str = "source"


@dataclass(frozen=True, slots=True)
class CandidateArtifactV1(_DictContract):
    artifact_id: str
    scope: str
    extractor_version: str
    input_hash: str
    source_refs: tuple[EvidenceRefV1, ...]
    claims: tuple["WikiClaimV3", ...]
    created_at: str
    model: str = ""
    prompt_hash: str = ""
    config_hash: str = ""


@dataclass(frozen=True, slots=True)
class WikiClaimV3(_DictContract):
    claim_id: str
    claim_type: ClaimType
    text: str
    status: ClaimStatus
    scope: str
    evidence: tuple[EvidenceRefV1, ...]
    symbols: tuple[str, ...] = ()
    venues: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()
    regimes: tuple[str, ...] = ()
    valid_from: str = ""
    valid_to: str = ""
    confidence: float = 0.0
    provenance_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(row.strip().upper() for row in self.symbols if row.strip()))
        object.__setattr__(self, "confidence", min(max(float(self.confidence), 0.0), 1.0))
        if self.status == "verified" and not self.evidence:
            raise WikiContractError("verified_claim_requires_evidence")
        if self.status == "verified" and any(not row.content_hash for row in self.evidence):
            raise WikiContractError("verified_claim_requires_hashed_evidence")


@dataclass(frozen=True, slots=True)
class WikiRelationshipV1(_DictContract):
    source_claim_id: str
    relationship_type: Literal[
        "supports", "contradicts", "supersedes", "depends_on", "applies_to"
    ]
    target_id: str


@dataclass(frozen=True, slots=True)
class JueWikiPageV3(_DictContract):
    page_id: str
    page_type: str
    scope: str
    title: str
    summary: str
    claims: tuple[WikiClaimV3, ...]
    relationships: tuple[WikiRelationshipV1, ...]
    status: ClaimStatus
    schema_version: str
    compiler_version: str


@dataclass(frozen=True, slots=True)
class WikiSnapshotV1(_DictContract):
    snapshot_id: str
    scope: str
    candidate_artifact_ids: tuple[str, ...]
    pages: tuple[JueWikiPageV3, ...]
    schema_version: str
    compiler_version: str
    created_at: str


@dataclass(frozen=True, slots=True)
class WikiContextRequestV1(_DictContract):
    target_scope: str
    symbols: tuple[str, ...]
    page_types: tuple[str, ...] = ()
    lanes: tuple[str, ...] = ()
    regimes: tuple[str, ...] = ()
    block_ids: tuple[str, ...] = ()
    horizons: tuple[str, ...] = ()
    max_chars: int = 24_000

    def __post_init__(self) -> None:
        if int(self.max_chars) <= 0:
            raise WikiContractError("wiki_context_max_chars_must_be_positive")
        object.__setattr__(self, "symbols", tuple(row.strip().upper() for row in self.symbols if row.strip()))


@dataclass(frozen=True, slots=True)
class WikiContextPacketV1(_DictContract):
    status: str
    read_mode: ReadMode
    snapshot_id: str
    selected_pages: tuple[JueWikiPageV3, ...]
    rejected_page_ids: tuple[str, ...]
    coverage_status: str
    quality_warnings: tuple[str, ...]
    repair_required: bool
    char_count: int
    required_eligible: bool = False
    version: str = "wiki_context_packet_v1"


@dataclass(frozen=True, slots=True)
class WikiDecisionGateV1(_DictContract):
    allow_new_risk: bool
    allow_exit_actions: bool
    reason: str
    read_mode: ReadMode
    snapshot_id: str
    version: str = "wiki_decision_gate_v1"
```

Apply the same non-empty identifier validation to evidence, artifacts, claims, pages, and snapshots in their `__post_init__` methods. The tests must cover each rejected empty identifier.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest tests/test_jue_wiki_contract.py -q`

Expected: all tests pass.

Run: `ruff check src/tradecraft/services/jue_wiki_contract.py tests/test_jue_wiki_contract.py`

Expected: no diagnostics.

- [ ] **Step 5: Review checkpoint**

Confirm the task changes only the two listed files. If commits are authorized later, use `feat(wiki): add evidence-backed V3 contracts`.

### Task 2: Add the transactional V3 repository without replacing legacy pages

**Files:**
- Create: `src/tradecraft/services/jue_wiki_repository.py`
- Create: `tests/test_jue_wiki_repository.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki.py`

**Interfaces:**
- Consumes: Task 1 transport contracts.
- Produces: `JueWikiRepository.initialize()`, `register_evidence(ref)`, `evidence_ids()`, `store_candidate(artifact)`, `candidate_artifacts(artifact_ids)`, `publish_snapshot(snapshot)`, `current_snapshot(scope)`, `pages_for_snapshot(snapshot_id)`, and `open_read_only()`.

- [ ] **Step 1: Write failing repository tests**

```python
import sqlite3
from pathlib import Path

import pytest

from tradecraft.services.jue_wiki_contract import WikiSnapshotV1
from tradecraft.services.jue_wiki_repository import JueWikiRepository


def test_failed_snapshot_publish_keeps_previous_snapshot(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    first = WikiSnapshotV1(
        snapshot_id="snapshot:kis:1",
        scope="kis",
        candidate_artifact_ids=(),
        pages=(),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )
    repo.publish_snapshot(first)

    with pytest.raises(sqlite3.IntegrityError):
        repo.publish_snapshot(first)

    assert repo.current_snapshot("kis").snapshot_id == "snapshot:kis:1"


def test_read_only_connection_rejects_writes(tmp_path: Path) -> None:
    repo = JueWikiRepository(tmp_path / "wiki.db")
    repo.initialize()
    with repo.open_read_only() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE forbidden_write (id INTEGER)")
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `pytest tests/test_jue_wiki_repository.py -q`

Expected: collection fails because `JueWikiRepository` does not exist.

- [ ] **Step 3: Implement additive schema migration and atomic publication**

Add tables with `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS wiki_evidence_v1 (
    evidence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_candidate_artifacts_v1 (
    artifact_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_snapshots_v1 (
    snapshot_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    compiler_version TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wiki_snapshot_scope_published
ON wiki_snapshots_v1(scope) WHERE published = 1;
CREATE TABLE IF NOT EXISTS wiki_pages_v3 (
    snapshot_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, page_id)
);
```

`publish_snapshot()` must start `BEGIN IMMEDIATE`, insert the snapshot and every page, demote the prior published snapshot for the scope, promote the new snapshot, and commit. Roll back on any error. It must not update or delete legacy `wiki_pages`, `wiki_source_refs`, operational DBs, or source DBs.

Implement read-only connections using SQLite URI mode:

```python
@contextmanager
def open_read_only(self) -> Iterator[sqlite3.Connection]:
    uri = f"file:{quote(str(self.db_path.resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
```

Add `JueWikiService.repository()` as a compatibility-facade method returning `JueWikiRepository(self.config.db_path)`; do not route legacy `write_page()` through V3 yet.

- [ ] **Step 4: Run repository and legacy compatibility tests**

Run: `pytest tests/test_jue_wiki_repository.py tests/test_jue_wiki.py -q`

Expected: all tests pass and legacy compiled Markdown behavior is unchanged.

Run: `ruff check src/tradecraft/services/jue_wiki_repository.py src/tradecraft/services/jue_wiki.py tests/test_jue_wiki_repository.py`

Expected: no diagnostics.

- [ ] **Step 5: Verify test isolation**

Run: `pytest tests/test_runtime_test_isolation.py -q`

Expected: all tests pass and live `.runtime` checksums and mtimes are unchanged.

- [ ] **Step 6: Review checkpoint**

Inspect `git diff --check` and confirm all schema changes are additive. If commits are authorized later, use `feat(wiki): add transactional V3 repository`.

### Task 3: Compile, lint, and publish immutable Wiki snapshots

**Files:**
- Create: `src/tradecraft/services/jue_wiki_compiler.py`
- Create: `src/tradecraft/services/jue_wiki_lint.py`
- Create: `src/tradecraft/services/jue_wiki_projection.py`
- Create: `tests/test_jue_wiki_compiler.py`
- Create: `tests/test_jue_wiki_lint.py`
- Create: `tests/test_jue_wiki_projection.py`

**Interfaces:**
- Consumes: Task 1 contracts and Task 2 repository.
- Produces: `JueWikiCompilerV1.compile(scope, artifacts, base_snapshot)`, `JueWikiPublisherV1.compile_and_publish(scope, artifact_ids)`, `WikiPublicationError`, `WikiLintFindingV1`, `lint_snapshot(snapshot)`, and `JueWikiProjectionWriter.project(snapshot)`.

- [ ] **Step 1: Write failing determinism and conflict tests**

```python
def _artifact(*, artifact_id: str, claim_id: str, text: str) -> CandidateArtifactV1:
    evidence = EvidenceRefV1(
        evidence_id=f"evidence:{artifact_id}",
        source_type="test",
        source_id=artifact_id,
        content_hash="a" * 64,
        observed_at="2026-07-11T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id=claim_id,
        claim_type="interpretation",
        text=text,
        status="verified",
        scope="kis",
        evidence=(evidence,),
        symbols=("005930",),
    )
    return CandidateArtifactV1(
        artifact_id=artifact_id,
        scope="kis",
        extractor_version="test_v1",
        input_hash="b" * 64,
        source_refs=(evidence,),
        claims=(claim,),
        created_at="2026-07-11T00:00:00+00:00",
    )


def test_same_candidate_set_compiles_to_same_page_payload() -> None:
    candidate_artifact = _artifact(
        artifact_id="one",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    compiler = JueWikiCompilerV1()
    first = compiler.compile(
        scope="kis", artifacts=(candidate_artifact,), base_snapshot=None
    )
    second = compiler.compile(
        scope="kis", artifacts=(candidate_artifact,), base_snapshot=None
    )
    assert first.pages == second.pages


def test_contradicting_verified_claims_are_preserved_and_flagged() -> None:
    bullish_artifact = _artifact(
        artifact_id="bullish",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is positive.",
    )
    bearish_artifact = _artifact(
        artifact_id="bearish",
        claim_id="claim:kis:005930:direction",
        text="Revision direction is negative.",
    )
    snapshot = JueWikiCompilerV1().compile(
        scope="kis",
        artifacts=(bullish_artifact, bearish_artifact),
        base_snapshot=None,
    )
    page = snapshot.pages[0]
    assert {claim.status for claim in page.claims} == {"conflicted"}
    assert any(
        relation.relationship_type == "contradicts"
        for relation in page.relationships
    )
```

```python
def test_lint_rejects_verified_claim_with_unknown_evidence() -> None:
    valid_snapshot = JueWikiCompilerV1().compile(
        scope="kis",
        artifacts=(
            _artifact(
                artifact_id="one",
                claim_id="claim:kis:005930:direction",
                text="Revision direction is positive.",
            ),
        ),
        base_snapshot=None,
    )
    findings = lint_snapshot(valid_snapshot, known_evidence_ids=set())
    assert [(row.severity, row.finding_type) for row in findings] == [
        ("error", "unresolved_evidence")
    ]
```

- [ ] **Step 2: Run the tests and confirm missing implementation failures**

Run: `pytest tests/test_jue_wiki_compiler.py tests/test_jue_wiki_lint.py -q`

Expected: collection fails for missing compiler and lint modules.

- [ ] **Step 3: Implement a pure compiler**

Group claims by the stable page key `(scope, page_type, primary_symbol_or_topic)`; sort artifacts by `artifact_id`, claims by `claim_id`, evidence by `evidence_id`, and relationships by their full tuple. Derive page and snapshot IDs from SHA-256 hashes of canonical JSON using sorted keys and compact separators.

Conflict rules are exact:

- the same `claim_id` and the same normalized text deduplicate;
- the same semantic key with opposing normalized text preserves both claims as `conflicted`;
- explicit `supersedes` preserves the older claim as `superseded`;
- compiler output never deletes a claim solely because it is absent from the newest artifact.

The compiler must not open SQLite or write Markdown.

- [ ] **Step 4: Implement lint and publication orchestration**

```python
@dataclass(frozen=True, slots=True)
class WikiLintFindingV1:
    severity: Literal["warning", "error"]
    finding_type: str
    page_id: str
    claim_id: str = ""
    message: str = ""


class JueWikiPublisherV1:
    def compile_and_publish(
        self,
        *,
        scope: str,
        artifact_ids: tuple[str, ...],
    ) -> WikiSnapshotV1:
        artifacts = self.repository.candidate_artifacts(artifact_ids)
        snapshot = self.compiler.compile(
            scope=scope,
            artifacts=artifacts,
            base_snapshot=self.repository.current_snapshot(scope),
        )
        findings = lint_snapshot(
            snapshot,
            known_evidence_ids=self.repository.evidence_ids(),
        )
        if any(row.severity == "error" for row in findings):
            raise WikiPublicationError("wiki_snapshot_lint_failed")
        self.repository.publish_snapshot(snapshot)
        return snapshot
```

Lint errors cover unresolved verified evidence, empty hashes, invalid lifecycle transitions, cross-scope claim leakage, dangling relationships, and duplicate IDs. Warnings cover stale claims, orphan pages, missing counter-theses, and low confidence.

- [ ] **Step 5: Implement deterministic Markdown and search projections**

`JueWikiProjectionWriter.project(snapshot)` writes a snapshot-scoped temporary directory, renders each page from structured claims and relationships, builds `index.md`, `contradictions.md`, and an FTS5 index, fsyncs the outputs, then atomically promotes the directory. Projection failure leaves the prior projection intact. `rebuild_index(snapshot)` deletes only disposable index artifacts and recreates equivalent index rows from the supplied published snapshot.

Add this recovery test to `tests/test_jue_wiki_projection.py`:

```python
def test_index_rebuild_is_equivalent(tmp_path: Path) -> None:
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:direction",
        claim_type="interpretation",
        text="Revision direction is positive.",
        status="draft",
        scope="kis",
        evidence=(),
        symbols=("005930",),
    )
    page = JueWikiPageV3(
        page_id="kis.symbol.005930",
        page_type="symbol",
        scope="kis",
        title="005930",
        summary="Positive revision direction.",
        claims=(claim,),
        relationships=(),
        status="draft",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )
    published_snapshot = WikiSnapshotV1(
        snapshot_id="snapshot:kis:1",
        scope="kis",
        candidate_artifact_ids=(),
        pages=(page,),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )
    writer = JueWikiProjectionWriter(tmp_path / "projection")
    first = writer.project(published_snapshot)
    writer.index_path.unlink()
    second = writer.rebuild_index(published_snapshot)
    assert second.row_hashes == first.row_hashes
```

- [ ] **Step 6: Run focused tests and lint**

Run: `pytest tests/test_jue_wiki_compiler.py tests/test_jue_wiki_lint.py tests/test_jue_wiki_projection.py tests/test_jue_wiki_repository.py -q`

Expected: all tests pass.

Run: `ruff check src/tradecraft/services/jue_wiki_compiler.py src/tradecraft/services/jue_wiki_lint.py src/tradecraft/services/jue_wiki_projection.py tests/test_jue_wiki_compiler.py tests/test_jue_wiki_lint.py tests/test_jue_wiki_projection.py`

Expected: no diagnostics.

- [ ] **Step 7: Review checkpoint**

Confirm a lint error cannot change the published snapshot. If commits are authorized later, use `feat(wiki): compile and lint immutable snapshots`.

### Task 4: Turn Naver and crypto research into versioned candidate artifacts

**Files:**
- Create: `src/tradecraft/services/jue_wiki_sources.py`
- Create: `tests/test_jue_wiki_sources.py`
- Modify: `src/tradecraft/services/kis_research_packet.py`
- Test: `tests/test_kis_research_packet.py`

**Interfaces:**
- Consumes: `KisResearchRepository`, `KisResearchPacketV2`, crypto research DB paths, and Task 1 contracts.
- Produces: `NaverWikiSourceAdapter.collect(symbols, observed_at)` and `CryptoWikiSourceAdapter.collect(symbols, observed_at)` returning `tuple[CandidateArtifactV1, ...]`, plus `JueWikiBackfillService.run(scope, cursor, limit, dry_run)`.

- [ ] **Step 1: Write failing source-adapter tests**

```python
class _NaverResearchRepository:
    def __init__(self) -> None:
        self.read_count = 0

    def latest_symbol_linked_reports(
        self, symbol: str, *, limit: int
    ) -> list[dict[str, Any]]:
        self.read_count += 1
        return [{
            "report_id": 42,
            "symbol": symbol,
            "published_at": "2026-07-10T00:00:00+00:00",
            "broker": "example",
            "link_confidence": 0.99,
            "pdf_sha256": "f" * 64,
            "pdf_archived_path": "/evidence/report-42.pdf",
        }][:limit]

    def get_report_facts(self, report_id: int) -> dict[str, Any] | None:
        self.read_count += 1
        return {
            "rating": "BUY",
            "target_price": {"value": 100_000},
            "catalysts": ["revision up"],
            "risks": ["demand slowdown"],
            "evidence_quotes": ["forecast revised"],
        } if report_id == 42 else None


def test_naver_adapter_preserves_report_hash_and_source_identity() -> None:
    repo = _NaverResearchRepository()
    artifacts = NaverWikiSourceAdapter(repo).collect(
        symbols=("005930",), observed_at="2026-07-11T00:00:00+00:00"
    )
    evidence = artifacts[0].source_refs[0]
    assert evidence.evidence_id == "naver-report:42"
    assert evidence.content_hash == "f" * 64
    assert artifacts[0].claims[0].evidence == (evidence,)


def test_source_adapter_uses_only_repository_read_contract() -> None:
    repo = _NaverResearchRepository()
    NaverWikiSourceAdapter(repo).collect(
        symbols=("005930",), observed_at="2026-07-11T00:00:00+00:00"
    )
    assert repo.read_count == 2
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `pytest tests/test_jue_wiki_sources.py -q`

Expected: collection fails because the source adapters do not exist.

- [ ] **Step 3: Expose a stable KIS research evidence conversion**

Add to `kis_research_packet.py`:

```python
def kis_packet_candidate_claims(
    packet: KisResearchPacketV2,
    *,
    artifact_id: str,
) -> tuple[WikiClaimV3, ...]:
    """Convert source-linked packet facts into draft or verified Wiki claims."""
    evidence_refs = tuple(
        EvidenceRefV1(
            evidence_id=f"naver-report:{row.report_id}",
            source_type="naver_report",
            source_id=str(row.report_id),
            content_hash=str(row.source_ref.get("pdf_sha256") or ""),
            observed_at=row.published_at,
            source_path=str(row.source_ref.get("pdf_archived_path") or ""),
            hash_origin="source",
        )
        for row in packet.evidence
        if str(row.source_ref.get("pdf_sha256") or "")
    )
    claims: list[WikiClaimV3] = []
    for index, text in enumerate(packet.confirmed_facts):
        claims.append(
            WikiClaimV3(
                claim_id=f"{artifact_id}:fact:{index}",
                claim_type="fact",
                text=text,
                status="verified" if evidence_refs else "draft",
                scope="kis",
                evidence=evidence_refs,
                symbols=(packet.symbol,),
                provenance_id=artifact_id,
            )
        )
    for claim_type, rows in (
        ("interpretation", packet.interpretation),
        ("hypothesis", packet.missing_data),
    ):
        for index, text in enumerate(rows):
            claims.append(
                WikiClaimV3(
                    claim_id=f"{artifact_id}:{claim_type}:{index}",
                    claim_type=claim_type,
                    text=text,
                    status="draft",
                    scope="kis",
                    evidence=evidence_refs,
                    symbols=(packet.symbol,),
                    provenance_id=artifact_id,
                )
            )
    return tuple(claims)
```

Confirmed facts with a source hash become `verified` fact claims. Interpretations become `draft` interpretation claims until compiler and lint validation. Missing data becomes a hypothesis gap and never becomes positive entry support.

- [ ] **Step 4: Implement read-only source adapters**

Open configured source SQLite files using `mode=ro`. Normalize Naver IDs as `naver-report:{report_id}` and crypto IDs as `{source_type}:{source_id}`. Use an existing SHA-256 from the source row when available and set `EvidenceRefV1.hash_origin="source"`; otherwise hash the immutable normalized payload and set `hash_origin="normalized_payload"`.

Artifact identity must be SHA-256 over source IDs, source hashes, extractor version, model, prompt hash, and configuration hash. Collecting the same inputs twice returns the same artifact ID and payload.

- [ ] **Step 5: Implement bounded, resumable backfill**

```python
@dataclass(frozen=True, slots=True)
class WikiBackfillBatchV1:
    scope: str
    input_cursor: str
    next_cursor: str
    artifact_ids: tuple[str, ...]
    source_count: int
    dry_run: bool


class WikiBackfillSource(Protocol):
    def read_after(
        self, *, scope: str, cursor: str, limit: int
    ) -> tuple[list[dict[str, Any]], str]:
        raise NotImplementedError


class JueWikiBackfillService:
    def __init__(
        self,
        *,
        source: WikiBackfillSource,
        artifact_builder: Callable[[dict[str, Any]], CandidateArtifactV1],
        repository: JueWikiRepository,
    ) -> None:
        self.source = source
        self.artifact_builder = artifact_builder
        self.repository = repository

    def run(
        self,
        *,
        scope: str,
        cursor: str,
        limit: int = 100,
        dry_run: bool = True,
    ) -> WikiBackfillBatchV1:
        bounded_limit = min(max(int(limit), 1), 100)
        rows, next_cursor = self.source.read_after(
            scope=scope,
            cursor=cursor,
            limit=bounded_limit,
        )
        artifacts = tuple(self.artifact_builder(row) for row in rows)
        if not dry_run:
            for artifact in artifacts:
                self.repository.store_candidate(artifact)
        return WikiBackfillBatchV1(
            scope=scope,
            input_cursor=cursor,
            next_cursor=next_cursor,
            artifact_ids=tuple(row.artifact_id for row in artifacts),
            source_count=len(rows),
            dry_run=bool(dry_run),
        )
```

Clamp `limit` to `1..100`. Order sources by stable source identity, return a durable next cursor, and store artifacts only when `dry_run=False`. Replaying the same cursor and source set produces the same artifact IDs. Backfill reads source databases in `mode=ro`, never publishes a snapshot itself, and never advances a stored checkpoint until artifact persistence succeeds.

Add tests proving the default is dry-run, batches never exceed 100 sources, a failed artifact write retains the previous cursor, and a repeated batch is idempotent.

- [ ] **Step 6: Run source and KIS packet regression tests**

Run: `pytest tests/test_jue_wiki_sources.py tests/test_kis_research_packet.py -q`

Expected: all tests pass.

Run: `ruff check src/tradecraft/services/jue_wiki_sources.py src/tradecraft/services/kis_research_packet.py tests/test_jue_wiki_sources.py`

Expected: no diagnostics.

- [ ] **Step 7: Review checkpoint**

Verify source DB checksums and mtimes do not change. If commits are authorized later, use `feat(wiki): ingest versioned research evidence`.

### Task 5: Add Wiki-first selection and the common new-risk gate

**Files:**
- Create: `src/tradecraft/services/jue_wiki_context.py`
- Create: `tests/test_jue_wiki_context.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Test: `tests/test_jue_wiki.py`

**Interfaces:**
- Consumes: Task 1 context contracts and Task 2 read-only repository.
- Produces: `JueWikiContextService.context_packet(request, read_mode)`, `evaluate_wiki_decision_gate(packet)`, `strip_direct_raw_rag_context(prompt)`, and compatibility `JueWikiService.context_pack(...)` V3 metadata.

- [ ] **Step 1: Write failing selection and gate tests**

```python
def _missing_packet(read_mode: str) -> WikiContextPacketV1:
    return WikiContextPacketV1(
        status="missing",
        read_mode=read_mode,
        snapshot_id="",
        selected_pages=(),
        rejected_page_ids=(),
        coverage_status="insufficient",
        quality_warnings=("wiki_snapshot_missing",),
        repair_required=True,
        char_count=0,
    )


@pytest.mark.parametrize("mode", ["shadow", "prefer"])
def test_non_required_mode_does_not_replace_existing_safety_behavior(mode) -> None:
    packet = _missing_packet(read_mode=mode)
    gate = evaluate_wiki_decision_gate(packet)
    assert gate.allow_new_risk is True
    assert gate.allow_exit_actions is True


def test_required_mode_blocks_new_risk_when_coverage_is_missing() -> None:
    gate = evaluate_wiki_decision_gate(_missing_packet(read_mode="required"))
    assert gate.allow_new_risk is False
    assert gate.allow_exit_actions is True
    assert gate.reason == "wiki_required_coverage_missing"


def test_required_mode_blocks_new_risk_before_shadow_eligibility() -> None:
    packet = WikiContextPacketV1(
        status="ok",
        read_mode="required",
        snapshot_id="snapshot:kis:1",
        selected_pages=(),
        rejected_page_ids=(),
        coverage_status="sufficient",
        quality_warnings=(),
        repair_required=False,
        char_count=0,
        required_eligible=False,
    )
    gate = evaluate_wiki_decision_gate(packet)
    assert gate.allow_new_risk is False
    assert gate.reason == "wiki_required_mode_ineligible"


def test_stale_claim_is_warning_not_positive_entry_support(tmp_path: Path) -> None:
    evidence = EvidenceRefV1(
        evidence_id="naver-report:42",
        source_type="naver_report",
        source_id="42",
        content_hash="a" * 64,
        observed_at="2026-05-01T00:00:00+00:00",
    )
    claim = WikiClaimV3(
        claim_id="claim:kis:005930:direction",
        claim_type="interpretation",
        text="Revision direction was positive.",
        status="stale",
        scope="kis",
        evidence=(evidence,),
        symbols=("005930",),
    )
    page = JueWikiPageV3(
        page_id="kis.symbol.005930",
        page_type="symbol",
        scope="kis",
        title="005930",
        summary="Stale research only.",
        claims=(claim,),
        relationships=(),
        status="stale",
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
    )
    snapshot = WikiSnapshotV1(
        snapshot_id="snapshot:kis:stale",
        scope="kis",
        candidate_artifact_ids=(),
        pages=(page,),
        schema_version="jue_wiki_page_v3",
        compiler_version="wiki_compiler_v1",
        created_at="2026-07-11T00:00:00+00:00",
    )
    repository = JueWikiRepository(tmp_path / "wiki.db")
    repository.initialize()
    repository.register_evidence(evidence)
    repository.publish_snapshot(snapshot)
    packet = JueWikiContextService(repository).context_packet(
        request=WikiContextRequestV1(target_scope="kis", symbols=("005930",)),
        read_mode="required",
    )
    assert packet.coverage_status == "insufficient"
    assert "stale_only_support" in packet.quality_warnings


def test_required_prompt_strips_only_direct_raw_rag_payloads() -> None:
    prompt, removed = strip_direct_raw_rag_context(
        {
            "live_account": {"cash": 1000},
            "jue_wiki": {"snapshot_id": "snapshot:kis:1"},
            "raw_reports": [{"content": "bulk report"}],
        }
    )
    assert removed == ("raw_reports",)
    assert "raw_reports" not in prompt
    assert prompt["live_account"] == {"cash": 1000}
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `pytest tests/test_jue_wiki_context.py -q`

Expected: collection fails because the context service does not exist.

- [ ] **Step 3: Implement deterministic selection and coverage rules**

Selection reads only the published snapshot. Rank exact symbol and scope matches first, then page type, regime, lane, horizon, freshness, confidence, and relationship relevance. Exclude rejected and superseded claims from positive support. Include stale and conflicted claims only in `quality_warnings` and counter-evidence.

Coverage is `sufficient` only when every requested symbol has at least one current verified fact or interpretation, all selected verified claims resolve to registered evidence, and no lint error affects the selected page. Respect `max_chars` while preserving claim types and evidence IDs.

`evaluate_wiki_decision_gate()` allows new risk in `required` only when coverage is sufficient and `required_eligible=True`. Missing coverage takes precedence as `wiki_required_coverage_missing`; otherwise a missing eligibility proof returns `wiki_required_mode_ineligible`. Both cases keep `allow_exit_actions=True`.

`strip_direct_raw_rag_context()` removes only the exact top-level keys `rag`, `rag_context`, `raw_rag`, `raw_reports`, `retrieved_documents`, and `research_documents`, plus nested objects explicitly marked `source_contract="raw_rag"`. It returns a copied prompt and sorted removed paths; it must not remove live account, market, order, safety, or `jue_wiki` fields.

- [ ] **Step 4: Add compatibility metadata to `context_pack()`**

Keep all existing fields and add:

```python
{
    "wiki_context_contract": packet.to_dict(),
    "snapshot_id": packet.snapshot_id,
    "read_mode": packet.read_mode,
    "coverage_status": packet.coverage_status,
    "repair_required": packet.repair_required,
}
```

Legacy callers without V3 tables receive the existing payload plus `read_mode="shadow"`, `coverage_status="legacy"`, and an empty snapshot ID. Do not change selection behavior for those callers.

- [ ] **Step 5: Run focused and compatibility tests**

Run: `pytest tests/test_jue_wiki_context.py tests/test_jue_wiki.py -q`

Expected: all tests pass.

Run: `ruff check src/tradecraft/services/jue_wiki_context.py src/tradecraft/services/jue_wiki.py tests/test_jue_wiki_context.py`

Expected: no diagnostics.

- [ ] **Step 6: Review checkpoint**

Confirm only `required` can create a new Wiki-specific risk block. If commits are authorized later, use `feat(wiki): add Wiki-first context and risk gate`.

### Task 6: Wire V3 compilation into the Wiki runner and stored readiness snapshot

**Files:**
- Modify: `src/tradecraft/runtime/jue_wiki_runner.py`
- Modify: `src/tradecraft/services/jue_wiki.py`
- Create: `tests/test_jue_wiki_runner_v3.py`
- Test: `tests/test_jue_wiki_runner.py`

**Interfaces:**
- Consumes: Tasks 2 through 5 services.
- Produces: `run_v3_scope(service, scope, adapters, publisher, projection_writer)`, runner steps `v3_ingest`, `v3_compile`, `v3_lint`, `v3_publish`, and stored Wiki V3 status.

- [ ] **Step 1: Write failing runner transaction tests**

```python
def _service(tmp_path: Path) -> JueWikiService:
    service = JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "pages",
            db_path=tmp_path / "wiki.db",
        )
    )
    service.initialize()
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


class _FailingPublisher:
    def compile_and_publish(
        self, *, scope: str, artifact_ids: tuple[str, ...]
    ) -> WikiSnapshotV1:
        raise WikiPublicationError("wiki_snapshot_lint_failed")


class _PublishingPublisher:
    def __init__(self, repository: JueWikiRepository, snapshot: WikiSnapshotV1) -> None:
        self.repository = repository
        self.snapshot = snapshot

    def compile_and_publish(
        self, *, scope: str, artifact_ids: tuple[str, ...]
    ) -> WikiSnapshotV1:
        self.repository.publish_snapshot(self.snapshot)
        return self.snapshot


def test_v3_compile_failure_preserves_previous_snapshot(tmp_path: Path) -> None:
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
```

- [ ] **Step 2: Run the tests and confirm missing V3 steps**

Run: `pytest tests/test_jue_wiki_runner_v3.py -q`

Expected: assertions fail because `v3_publish` is absent.

- [ ] **Step 3: Add bounded V3 runner steps**

Run source ingestion for `kis` and `binance`, store candidate artifacts, compile both scopes, lint, and publish only passing snapshots. Keep the existing legacy `rebuild`, `lint`, `repair`, playbook, performance, and application steps during `shadow` and `prefer` migration.

Each `_run_step` payload must include scope, candidate count, snapshot ID, page count, warning count, elapsed time, and error message. One scope failing must not prevent the other scope from publishing.

- [ ] **Step 4: Persist read-only status projection**

Extend `project_status_snapshot()` output with:

```python
{
    "v3": {
        "published_by_scope": {
            "kis": "snapshot:kis:current",
            "binance": "snapshot:binance:current",
        },
        "claim_status_counts": {},
        "stale_count": 0,
        "conflicted_count": 0,
        "orphan_page_count": 0,
        "repair_backlog_count": 0,
        "last_compile_status": "ok",
        "mode_eligibility": {"kis": {}, "binance": {}},
    }
}
```

`status()` and API reads consume only this stored projection. They must not call ingest, compile, lint, repair, or publish.

- [ ] **Step 5: Run runner and readiness regression tests**

Run: `pytest tests/test_jue_wiki_runner_v3.py tests/test_jue_wiki_runner.py tests/test_readiness_performance.py -q`

Expected: all tests pass and readiness writes remain zero.

Run: `ruff check src/tradecraft/runtime/jue_wiki_runner.py src/tradecraft/services/jue_wiki.py tests/test_jue_wiki_runner_v3.py`

Expected: no diagnostics.

- [ ] **Step 6: Review checkpoint**

Confirm no runner test touches live `.runtime`. If commits are authorized later, use `feat(wiki): publish V3 snapshots from runner`.

### Task 7: Integrate the read policy and venue-specific risk suppression

**Files:**
- Modify: `src/tradecraft/config.py`
- Modify: `.env.example`
- Modify: `docs/spec/12_config_env.md`
- Modify: `src/tradecraft/runtime/kis_block_trader_runner.py`
- Modify: `src/tradecraft/runtime/binance_block_trader_runner.py`
- Modify: `src/tradecraft/services/kis_block_trader.py`
- Modify: `src/tradecraft/services/binance_block_trader.py`
- Create: `tests/test_jue_wiki_manager_gate.py`
- Test: `tests/test_config.py`
- Test: `tests/test_kis_block_trader.py`
- Test: `tests/test_binance_block_trader.py`

**Interfaces:**
- Consumes: `WikiContextPacketV1` and `WikiDecisionGateV1` from Tasks 1 and 5.
- Produces: `AppSettings.jue_wiki_read_mode`, manager prompt field `jue_wiki_decision_gate`, and venue-specific suppression audit rows.

- [ ] **Step 1: Write failing configuration and manager-gate tests**

```python
def _blocked_gate() -> WikiDecisionGateV1:
    return WikiDecisionGateV1(
        allow_new_risk=False,
        allow_exit_actions=True,
        reason="wiki_required_coverage_missing",
        read_mode="required",
        snapshot_id="snapshot:kis:1",
    )


def test_jue_wiki_read_mode_defaults_to_shadow() -> None:
    assert AppSettings(_env_file=None).jue_wiki_read_mode == "shadow"


def test_wiki_promotion_thresholds_default_to_disabled() -> None:
    assert AppSettings(_env_file=None).jue_wiki_promotion_thresholds_json == "{}"


def test_kis_required_wiki_gap_blocks_create_but_keeps_close_actions() -> None:
    actions = {
        "create_blocks": [{"symbol": "005930"}],
        "close_blocks": [{"block_id": "kis-1"}],
    }
    filtered, audit = apply_kis_wiki_decision_gate(actions, _blocked_gate())
    assert filtered["create_blocks"] == []
    assert filtered["close_blocks"] == [{"block_id": "kis-1"}]
    assert audit["suppressed_new_risk_count"] == 1


def test_binance_required_wiki_gap_keeps_reduce_only_actions() -> None:
    actions = {
        "create_blocks": [{"symbol": "BTCUSDT"}],
        "update_blocks": [{"block_id": "btc-1", "reduce_only": True}],
    }
    filtered, _ = apply_binance_wiki_decision_gate(actions, _blocked_gate())
    assert filtered["create_blocks"] == []
    assert filtered["update_blocks"][0]["reduce_only"] is True
```

- [ ] **Step 2: Run the tests and confirm failures**

Run: `pytest tests/test_jue_wiki_manager_gate.py tests/test_config.py -q`

Expected: failures for the missing setting and gate functions.

- [ ] **Step 3: Add the backward-compatible setting**

```python
jue_wiki_read_mode: str = Field(
    default="shadow",
    validation_alias=AliasChoices(
        "TRADECRAFT_JUE_WIKI_READ_MODE",
        "jue_wiki_read_mode",
    ),
)

jue_wiki_promotion_thresholds_json: str = Field(
    default="{}",
    validation_alias=AliasChoices(
        "TRADECRAFT_JUE_WIKI_PROMOTION_THRESHOLDS_JSON",
        "jue_wiki_promotion_thresholds_json",
    ),
)
```

Validate only `shadow`, `prefer`, and `required`. Do not map or rename `jue_wiki_prompt_mode`; its `observe`, `assist`, and `primary` values remain unchanged. Promotion thresholds are a JSON object keyed by venue and playbook type, for example `{"kis":{"swing":30},"binance":{"intraday":50}}`. Missing, zero, negative, boolean, or malformed values prohibit automatic promotion and surface a configuration warning.

- [ ] **Step 4: Attach the decision gate to both manager prompts**

The runner provider passes `read_mode` into the V3 context service. In `required`, it calls `strip_direct_raw_rag_context()` before LLM invocation and records removed paths. Each manager adds `jue_wiki_decision_gate` to `decision_inputs` and instructs the model that blocked new-risk actions will be rejected while close, pause, reduce-only, reconciliation, and kill-switch behavior remain valid.

- [ ] **Step 5: Apply venue-specific action filtering after contract validation**

Implement separate functions in the KIS and Binance modules. KIS suppresses `create_blocks` and quantity-increasing updates. Binance suppresses `create_blocks`, leverage increases, and non-reduce-only quantity increases. Both preserve close, pause, cancel, stop tightening, and reduce-only actions.

Record every suppressed action with venue, symbol or block ID, snapshot ID, read mode, and `wiki_required_coverage_missing`. Never silently transform a blocked create into another action.

- [ ] **Step 6: Run focused venue regressions**

Run: `pytest tests/test_jue_wiki_manager_gate.py tests/test_config.py -q`

Expected: all tests pass.

Run: `pytest tests/test_kis_block_trader.py -k "wiki or manager_create or close" -q`

Expected: all selected tests pass.

Run: `pytest tests/test_binance_block_trader.py -k "wiki or manager_create or reduce_only" -q`

Expected: all selected tests pass.

Run: `ruff check src/tradecraft/config.py src/tradecraft/runtime/kis_block_trader_runner.py src/tradecraft/runtime/binance_block_trader_runner.py src/tradecraft/services/kis_block_trader.py src/tradecraft/services/binance_block_trader.py tests/test_jue_wiki_manager_gate.py`

Expected: no diagnostics.

- [ ] **Step 7: Review checkpoint**

Confirm default `shadow` produces no action changes and `required` has not been activated. If commits are authorized later, use `feat(wiki): enforce venue-safe Wiki read policy`.

### Task 8: Record shadow comparisons and enforce per-venue eligibility

**Files:**
- Create: `src/tradecraft/services/jue_wiki_shadow.py`
- Create: `scripts/replay_jue_wiki.py`
- Create: `tests/test_jue_wiki_shadow.py`
- Modify: `src/tradecraft/services/manager_run_telemetry.py`
- Test: `tests/test_manager_run_telemetry.py`
- Modify: `src/tradecraft/services/jue_wiki_application.py`
- Test: `tests/test_jue_wiki_application.py`
- Modify: `src/tradecraft/services/jue_wiki_context.py`
- Test: `tests/test_jue_wiki_context.py`

**Interfaces:**
- Consumes: recorded manager prompts, actions, Wiki packets, safety-gate summaries, and fill provenance.
- Produces: `WikiShadowComparisonV1`, `replay_shadow_record(recording, complete_json)`, `JueWikiShadowStore.initialize()`, `JueWikiShadowStore.record()`, `JueWikiShadowStore.eligibility(venue)`, and extended `ManagerRunTelemetryV1` fields.

- [ ] **Step 1: Write failing eligibility tests**

```python
def _complete_comparison(
    *,
    venue: str,
    run_id: str,
    safety_gate_loss: tuple[str, ...] = (),
) -> WikiShadowComparisonV1:
    return WikiShadowComparisonV1(
        run_id=run_id,
        venue=venue,
        legacy_prompt_hash="a" * 64,
        wiki_prompt_hash="b" * 64,
        snapshot_id=f"snapshot:{venue}:1",
        legacy_action_hash="c" * 64,
        wiki_action_hash="d" * 64,
        safety_gate_loss=safety_gate_loss,
        direct_raw_rag_paths=(),
        comparison_status="complete",
        created_at="2026-07-11T00:00:00+00:00",
    )


def test_required_eligibility_is_calculated_per_venue(tmp_path: Path) -> None:
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    for index in range(500):
        store.record(_complete_comparison(venue="kis", run_id=f"kis:{index}"))
    assert store.eligibility("kis")["required_eligible"] is True
    assert store.eligibility("binance")["required_eligible"] is False


def test_unexplained_safety_gate_loss_blocks_eligibility(tmp_path: Path) -> None:
    store = JueWikiShadowStore(tmp_path / "wiki.db")
    store.initialize()
    for index in range(500):
        row = _complete_comparison(venue="binance", run_id=f"binance:{index}")
        store.record(row)
    store.record(
        _complete_comparison(
            venue="binance",
            run_id="binance:unsafe",
            safety_gate_loss=("max_exposure",),
        )
    )
    assert store.eligibility("binance")["required_eligible"] is False
    assert store.eligibility("binance")["reason"] == "safety_gate_divergence"
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `pytest tests/test_jue_wiki_shadow.py -q`

Expected: collection fails because the shadow store does not exist.

- [ ] **Step 3: Implement immutable shadow comparison records**

```python
@dataclass(frozen=True, slots=True)
class WikiShadowComparisonV1:
    run_id: str
    venue: Literal["kis", "binance"]
    legacy_prompt_hash: str
    wiki_prompt_hash: str
    snapshot_id: str
    legacy_action_hash: str
    wiki_action_hash: str
    safety_gate_loss: tuple[str, ...]
    direct_raw_rag_paths: tuple[str, ...]
    comparison_status: Literal["complete", "incomplete", "error"]
    created_at: str
```

Store comparisons in additive Wiki DB tables. Count only `complete` comparisons with non-empty snapshot IDs. Eligibility requires at least 500 complete comparisons for the requested venue, zero unexplained safety-gate losses, zero required-mode direct raw-RAG paths, full snapshot trace coverage, and zero Wiki-induced new-risk expansion during simulated outage.

- [ ] **Step 4: Implement isolated same-input replay**

`replay_shadow_record(recording, complete_json)` takes one stored manager runtime input, its legacy action result, and its Wiki snapshot ID. It rebuilds the Wiki-first prompt from that exact input, strips direct raw-RAG paths, calls the injected `complete_json` once, validates the response through the existing manager contract, compares candidates, actions, and safety gates, and returns `WikiShadowComparisonV1`. It never constructs an executor and never calls broker or exchange adapters.

`scripts/replay_jue_wiki.py` accepts `--venue kis|binance`, `--recording PATH`, `--output PATH`, and `--dry-run`. `--dry-run` is the default and writes no Wiki DB rows. The command rejects paths inside the live `.runtime` tree unless the input is opened read-only and the output is outside `.runtime`.

Add a test with a fake `complete_json` proving one Wiki completion, zero executor calls, identical recorded input hashes, and a complete comparison row.

- [ ] **Step 5: Extend telemetry without changing existing fields**

Add optional fields to `ManagerRunTelemetryV1`:

```python
wiki_read_mode: str = "shadow"
wiki_snapshot_id: str = ""
wiki_coverage_status: str = ""
wiki_context_chars: int = 0
wiki_shadow_comparison_id: str = ""
wiki_suppressed_new_risk_count: int = 0
```

Do not rename or remove current telemetry keys.

- [ ] **Step 6: Project eligibility but never activate it**

`JueWikiApplicationService.project_mode_recommendations()` may recommend `prefer` or `required_eligible`; it must not write `AppSettings`, `.env`, or runtime process configuration. Include exact blockers and per-venue sample counts. `JueWikiContextService` reads the stored eligibility result for its target scope and sets `WikiContextPacketV1.required_eligible`; absence, staleness, or a venue mismatch resolves to `False`. Policy or playbook promotion also requires a positive threshold from `jue_wiki_promotion_thresholds_json`, fill-proven closed samples at or above that threshold, complete cost attribution, and the existing policy-review gate. An absent threshold returns `automatic_promotion_allowed=False` with `reason="promotion_threshold_unconfigured"`.

- [ ] **Step 7: Run focused tests and lint**

Run: `pytest tests/test_jue_wiki_shadow.py tests/test_manager_run_telemetry.py tests/test_jue_wiki_context.py -q`

Expected: all tests pass.

Run: `pytest tests/test_jue_wiki_application.py -k "mode_recommendation or status" -q`

Expected: all selected tests pass.

Run: `ruff check src/tradecraft/services/jue_wiki_shadow.py src/tradecraft/services/manager_run_telemetry.py src/tradecraft/services/jue_wiki_application.py src/tradecraft/services/jue_wiki_context.py scripts/replay_jue_wiki.py tests/test_jue_wiki_shadow.py`

Expected: no diagnostics.

- [ ] **Step 8: Review checkpoint**

Confirm recommendations are read-only and per venue. If commits are authorized later, use `feat(wiki): add per-venue shadow eligibility`.

### Task 9: Expose stored health, verify recovery, and complete the migration documentation

**Files:**
- Modify: `src/tradecraft/api/ops_readiness.py`
- Modify: `src/tradecraft/api/ops_payloads.py`
- Modify: `tests/test_ops_readiness_signals.py`
- Modify: `tests/test_ops_payloads.py`
- Create: `tests/test_jue_wiki_failure_recovery.py`
- Modify: `docs/spec/08_research_memory.md`
- Modify: `docs/spec/09_runtime_processes.md`
- Modify: `docs/superpowers/plans/2026-07-10-hermes-continuous-implementation-log.md`

**Interfaces:**
- Consumes: stored Wiki V3 status and Task 8 eligibility.
- Produces: read-only readiness warnings, recovery evidence, and operator documentation.

- [ ] **Step 1: Write failing readiness and recovery tests**

```python
def test_readiness_warns_on_stale_or_conflicted_required_knowledge() -> None:
    payload = build_ops_jue_wiki_payload(
        enabled=True,
        status={
            "v3": {
                "read_mode": "required",
                "stale_count": 2,
                "conflicted_count": 1,
                "last_compile_status": "ok",
            }
        },
        runner={"alive": True},
        state_path=".runtime/jue_wiki_runner.json",
        interval_sec=1800,
    )
    assert "jue_wiki_required_knowledge_degraded" in payload["warnings"]


def test_wiki_outage_blocks_new_risk_but_preserves_exit_actions() -> None:
    packet = WikiContextPacketV1(
        status="error",
        read_mode="required",
        snapshot_id="",
        selected_pages=(),
        rejected_page_ids=(),
        coverage_status="insufficient",
        quality_warnings=("wiki_repository_unavailable",),
        repair_required=True,
        char_count=0,
    )
    gate = evaluate_wiki_decision_gate(packet)
    filtered, _ = apply_kis_wiki_decision_gate(
        {
            "create_blocks": [{"symbol": "005930"}],
            "close_blocks": [{"block_id": "existing-1"}],
        },
        gate,
    )
    assert filtered["create_blocks"] == []
    assert filtered["close_blocks"] == [{"block_id": "existing-1"}]
    assert gate.allow_exit_actions is True
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `pytest tests/test_ops_readiness_signals.py tests/test_ops_payloads.py tests/test_jue_wiki_failure_recovery.py -q`

Expected: failures for missing V3 readiness signals and recovery fixture behavior.

- [ ] **Step 3: Add stored readiness signals**

Expose publication age, stale count, conflicted count, orphan pages, failed compilation, repair backlog, index rebuild state, per-venue comparison count, per-venue eligibility, and active read mode. Warnings must identify scope and exact blocker.

Readiness functions accept already stored dictionaries. Tests monkeypatch compiler and repair methods to raise if called, proving the request path is read-only.

- [ ] **Step 4: Add failure-recovery integration coverage**

Cover source outage, compiler failure, lint failure, index loss and rebuild, Wiki database outage, stale-only support, conflict-only support, and rollback to a prior snapshot. For each, assert no new risk expansion and that exits, reconciliation, and kill-switch checks remain available.

- [ ] **Step 5: Update operator documentation and implementation log**

Document:

- the four ownership layers;
- V3 snapshot and evidence identities;
- the distinction between prompt mode and read mode;
- `shadow → prefer → required` acceptance gates;
- safe rollback to `shadow`;
- required-mode eligibility never changing live settings automatically;
- RAG retained for bounded repair, audit, backfill, and index rebuild.

Add changed files, exact verification commands, measured timings, and remaining risks to the continuous implementation log.

- [ ] **Step 6: Run focused API and documentation tests**

Run: `pytest tests/test_ops_readiness_signals.py tests/test_ops_payloads.py tests/test_jue_wiki_failure_recovery.py tests/test_docs_spec.py -q`

Expected: all tests pass.

Run: `ruff check src/tradecraft/api/ops_readiness.py src/tradecraft/api/ops_payloads.py tests/test_jue_wiki_failure_recovery.py`

Expected: no diagnostics.

- [ ] **Step 7: Run domain verification**

Run: `python scripts/verify.py domain --area jue`

Expected: exit code 0, no live `.runtime` access, and timing recorded.

- [ ] **Step 8: Run KIS and Binance domain verification**

Run: `python scripts/verify.py domain --area kis`

Expected: exit code 0.

Run: `python scripts/verify.py domain --area binance`

Expected: exit code 0.

- [ ] **Step 9: Run full verification**

Run: `python scripts/verify.py full`

Expected: exit code 0, all tests pass, and live `.runtime` checksums and mtimes are unchanged.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 10: Final safety review checkpoint**

Confirm the working configuration still uses `shadow`, no live order was sent, no runtime data was deleted, and no commit was created without explicit authorization. Record any remaining mode-eligibility blockers; do not bypass them. If commits are authorized later, use `docs(wiki): document Wiki-first operations and recovery` for documentation-only changes after code tasks have their own commits.

## Execution Order And Stop Conditions

Execute tasks in numeric order. Stop a task and repair it before continuing when any focused test fails, a live `.runtime` checksum changes, a source or operational DB is opened writable, a legacy API payload loses a field, an existing safety gate diverges, or a Wiki failure prevents exit handling.

Do not activate `prefer` merely because its implementation exists. Do not activate `required` merely because 500 comparisons exist. Eligibility also requires complete snapshot attribution, zero unexplained safety-gate loss, zero direct raw-RAG injection on the required path, and successful failure-recovery tests. Live mode changes remain a separate user-approved operation after this implementation plan completes.
