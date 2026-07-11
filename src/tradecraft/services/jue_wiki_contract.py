from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ClaimType = Literal["fact", "interpretation", "hypothesis", "policy"]
ClaimStatus = Literal[
    "draft", "verified", "stale", "conflicted", "superseded", "rejected"
]
ReadMode = Literal["shadow", "prefer", "required"]
WIKI_GATE_IDENTITY_MAX_CHARS = 120


class WikiContractError(ValueError):
    pass


class _DictContract:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_identifier(value: str, field_name: str) -> None:
    if not value.strip():
        raise WikiContractError(f"{field_name}_must_be_non_empty")


@dataclass(frozen=True, slots=True)
class EvidenceRefV1(_DictContract):
    evidence_id: str
    source_type: str
    source_id: str
    content_hash: str
    observed_at: str
    source_path: str = ""
    hash_origin: str = "source"

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence_id")


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
    relationships: tuple["WikiRelationshipV1", ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.artifact_id, "artifact_id")


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
        _require_identifier(self.claim_id, "claim_id")
        object.__setattr__(
            self,
            "symbols",
            tuple(row.strip().upper() for row in self.symbols if row.strip()),
        )
        object.__setattr__(
            self, "confidence", min(max(float(self.confidence), 0.0), 1.0)
        )
        if self.status == "verified" and not self.evidence:
            raise WikiContractError("verified_claim_requires_evidence")
        if self.status == "verified" and any(
            not row.content_hash.strip() for row in self.evidence
        ):
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

    def __post_init__(self) -> None:
        _require_identifier(self.page_id, "page_id")


@dataclass(frozen=True, slots=True)
class WikiSnapshotV1(_DictContract):
    snapshot_id: str
    scope: str
    candidate_artifact_ids: tuple[str, ...]
    pages: tuple[JueWikiPageV3, ...]
    schema_version: str
    compiler_version: str
    created_at: str

    def __post_init__(self) -> None:
        _require_identifier(self.snapshot_id, "snapshot_id")


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
        if int(self.max_chars) < 2:
            raise WikiContractError("wiki_context_max_chars_must_encode_empty_array")
        object.__setattr__(
            self,
            "symbols",
            tuple(row.strip().upper() for row in self.symbols if row.strip()),
        )


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
